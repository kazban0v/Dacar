"""Short, serialized financial transactions. No network/printing inside locks."""
import hashlib
import json
from contextlib import contextmanager
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction, OperationalError
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from analytics.models import AuditLog
from catalog.models import Product, StockMovement
from sales.models import FinancialLock, OperationReceipt, SaleOrder, SaleOrderItem

CENT = Decimal('0.01')
MAX_MONEY = Decimal('9999999999.99')


class OperationConflict(APIException):
    status_code = 409
    default_detail = 'Этот ключ уже использован для другой операции. Проверьте историю.'


class OperationBusy(APIException):
    status_code = 503
    default_detail = 'База занята. Повторите ту же операцию через несколько секунд.'


def money(value):
    value = value.quantize(CENT, rounding=ROUND_HALF_UP)
    if value < 0 or value > MAX_MONEY:
        raise ValidationError('Сумма выходит за допустимые пределы.')
    return value


@contextmanager
def financial_transaction():
    try:
        with transaction.atomic():
            # First SQL must acquire a write lock before reading any balances.
            if not FinancialLock.objects.filter(pk=1).update(id=1):
                FinancialLock.objects.create(pk=1)
            yield
    except OperationalError as exc:
        if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
            raise OperationBusy() from exc
        raise


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str,
        separators=(',', ':')).encode()).hexdigest()


def replay(request, kind, data):
    existing = OperationReceipt.objects.filter(key=data['client_sync_id']).first()
    if existing and (existing.actor_id != request.user.pk or existing.kind != kind
                     or existing.fingerprint != fingerprint(data)):
        raise OperationConflict()
    return existing


def checkout(request, data):
    with financial_transaction():
        existing = replay(request, 'checkout', data)
        if existing:
            if not existing.order_id:
                raise OperationConflict('Ранее созданный чек удалён. Повторная продажа заблокирована.')
            return existing.order, True

        rows = []
        subtotal = Decimal('0.00')
        item_discounts = Decimal('0.00')
        # Sorted acquisition order is also safe on row-locking databases.
        for item in sorted(data['items'], key=lambda row: row['product_id']):
            product = Product.objects.filter(pk=item['product_id'], is_active=True).first()
            if product is None:
                raise ValidationError('Товар не найден или деактивирован.')
            qty, discount = item['quantity'], item['discount']
            unit_price = money(product.retail_price)
            cost = money(product.purchase_price)
            line_subtotal = money(qty * unit_price)
            if discount > line_subtotal:
                raise ValidationError('Скидка на позицию больше её стоимости.')
            # Conditional SQL decrement is an additional guard against overdraft.
            updated = Product.objects.filter(pk=product.pk, stock_qty__gte=qty).update(
                stock_qty=F('stock_qty') - qty, updated_at=timezone.now())
            if not updated:
                raise ValidationError(f"Недостаточно товара '{product.name}' на складе.")
            subtotal += line_subtotal
            item_discounts += discount
            rows.append(dict(product=product, quantity=qty, unit_price=unit_price,
                purchase_price_snapshot=cost, discount_amount=discount,
                total_amount=line_subtotal - discount))

        subtotal = money(subtotal)
        discount = money(item_discounts + data['discount_amount'])
        if discount > subtotal:
            raise ValidationError('Общая скидка больше стоимости товаров.')
        total = money(subtotal - discount)
        paid = data['paid_amount']
        if paid < total:
            raise ValidationError('Внесённая сумма меньше итога чека.')
        if data['payment_method'] != SaleOrder.PaymentMethod.CASH and paid != total:
            raise ValidationError('Для безналичной или смешанной оплаты укажите точную сумму чека.')

        order = SaleOrder.objects.create(order_number=SaleOrder.generate_order_number(),
            cashier=request.user, payment_method=data['payment_method'], subtotal_amount=subtotal,
            discount_amount=discount, total_amount=total, paid_amount=paid,
            change_amount=money(paid - total), notes=data['notes'])
        for row in rows:
            SaleOrderItem.objects.create(order=order, **row)
            StockMovement.objects.create(product=row['product'], movement_type='SALE',
                quantity=-row['quantity'], cost_price=row['purchase_price_snapshot'],
                comment=f'Продажа по чеку {order.order_number}', created_by=request.user)
        AuditLog.log(request, AuditLog.ActionType.SALE,
            f'Проведен чек № {order.order_number} на сумму {order.total_amount:.2f} ₸ '
            f'(Способ оплаты: {order.get_payment_method_display()})')
        OperationReceipt.objects.create(key=data['client_sync_id'], actor=request.user,
            kind='checkout', fingerprint=fingerprint(data), order=order)
        return order, False


def refund(request, order_id, reason):
    with financial_transaction():
        order = SaleOrder.objects.get(pk=order_id)
        if order.status == SaleOrder.Status.REFUNDED:
            return order, True
        if order.status != SaleOrder.Status.COMPLETED:
            raise ValidationError('Возврат возможен только для проведённого чека.')
        items = list(order.items.select_related('product').order_by('product_id'))
        if any(item.product_id is None for item in items):
            raise ValidationError('Товар из чека удалён. Требуется восстановить карточку перед возвратом.')
        for item in items:
            if item.quantity <= 0:
                raise ValidationError('В старом чеке некорректное количество. Нужна ручная проверка.')
            if not Product.objects.filter(pk=item.product_id,
                    stock_qty__lte=Decimal('999999999.999') - item.quantity).update(
                    stock_qty=F('stock_qty') + item.quantity, updated_at=timezone.now()):
                raise ValidationError('Невозможно восстановить остаток товара.')
            StockMovement.objects.create(product_id=item.product_id, movement_type='RETURN',
                quantity=item.quantity, cost_price=item.purchase_price_snapshot,
                comment=f'Возврат {order.order_number}: {reason}'[:255], created_by=request.user)
        order.status = SaleOrder.Status.REFUNDED
        order.refund_reason = reason
        order.refunded_by = request.user
        order.refunded_at = timezone.now()
        order.save(update_fields=['status', 'refund_reason', 'refunded_by', 'refunded_at', 'updated_at'])
        AuditLog.log(request, AuditLog.ActionType.REFUND,
            f'Оформлен возврат по чеку № {order.order_number} на сумму {order.total_amount:.2f} ₸. Причина: {reason}')
        return order, False
