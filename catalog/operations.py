from decimal import Decimal
import uuid
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from analytics.models import AuditLog
from catalog.models import Product, StockMovement
from sales.models import OperationReceipt
from sales.operations import financial_transaction, replay, fingerprint, OperationConflict


class StockActionSerializer(serializers.Serializer):
    client_sync_id = serializers.RegexField(r'^[A-Za-z0-9_-]{16,64}$', required=False,
        default=lambda: uuid.uuid4().hex)
    product_id = serializers.IntegerField(min_value=1)
    action = serializers.ChoiceField(choices=['IN', 'OUT', 'TRANSFER_TO_SHOP', 'ADJUSTMENT', 'RETURN', 'SALE'])
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=0)
    cost_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0,
        required=False, allow_null=True, default=None)
    writeoff_reason = serializers.ChoiceField(choices=StockMovement.WriteOffReason.choices,
        required=False, allow_blank=True, default='')
    comment = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')

    def validate(self, data):
        if data['action'] != 'ADJUSTMENT' and data['quantity'] == 0:
            raise serializers.ValidationError('Количество должно быть больше нуля.')
        if data['action'] == StockMovement.MovementType.OUT and not data['writeoff_reason']:
            raise serializers.ValidationError({'writeoff_reason': 'Выберите причину списания.'})
        if data['action'] != StockMovement.MovementType.OUT:
            data['writeoff_reason'] = ''
        return data


def stock_action(request, data):
    with financial_transaction():
        existing = replay(request, 'stock', data)
        if existing:
            return {**existing.result, 'replayed': True}
        # Old mobile keys are retained in stock movements. Never apply them again.
        if StockMovement.objects.filter(client_sync_id=data['client_sync_id']).exists():
            raise OperationConflict('Операция уже есть в старом журнале. Проверьте складскую историю.')
        product = Product.objects.filter(pk=data['product_id'], is_active=True).first()
        if product is None:
            raise NotFound('Товар не найден или деактивирован.')
        before, qty, action = product.stock_qty, data['quantity'], data['action']
        after = qty if action == 'ADJUSTMENT' else before + (qty if action in {'IN', 'RETURN'} else -qty)
        if after < 0:
            raise OperationConflict({'error_code': 'INSUFFICIENT_STOCK',
                'available_stock': str(before), 'message': 'Недостаточно товара на складе.'})
        if after > Decimal('999999999.999'):
            raise serializers.ValidationError('Остаток превышает допустимый предел.')
        Product.objects.filter(pk=product.pk).update(stock_qty=after, updated_at=timezone.now())
        label = dict(StockMovement.MovementType.choices)[action]
        reason = data.get('writeoff_reason', '')
        StockMovement.objects.create(product=product, movement_type=action, quantity=qty,
            cost_price=data['cost_price'] if data['cost_price'] is not None else product.purchase_price,
            writeoff_reason=reason, comment=data['comment'], created_by=request.user,
            client_sync_id=data['client_sync_id'])
        audit_type = 'STOCK_ADJUST' if action == 'ADJUSTMENT' else ('STOCK_IN' if action == 'IN' else 'STOCK_OUT')
        reason_text = ''
        if reason:
            reason_text = f' Причина: {dict(StockMovement.WriteOffReason.choices)[reason]}.'
        AuditLog.log(request, audit_type,
            f'{label}: «{product.name}» — {qty} {product.unit} (было {before}, стало {after}).'
            f'{reason_text} {data["comment"]}'.strip())
        result = {'success': True, 'message': f'{label}: «{product.name}» — выполнено.',
            'product_id': product.pk, 'product_name': product.name, 'stock_qty': float(after),
            'prev_stock': float(before), 'unit': product.unit, 'is_low_stock': after <= product.min_stock_alert}
        OperationReceipt.objects.create(key=data['client_sync_id'], actor=request.user,
            kind='stock', fingerprint=fingerprint(data), result=result)
        return {**result, 'replayed': False}


def reverse_writeoff(request, movement_id):
    """Restore stock from one write-off exactly once and keep an immutable trail."""
    with financial_transaction():
        movement = StockMovement.objects.select_related('product').filter(
            pk=movement_id,
            movement_type=StockMovement.MovementType.OUT,
        ).first()
        if movement is None:
            raise NotFound('Списание не найдено.')
        if movement.reversed_at:
            return {'success': True, 'replayed': True, 'movement': movement}
        if movement.quantity <= 0:
            raise serializers.ValidationError('У списания некорректное количество. Нужна ручная проверка.')

        product = movement.product
        before = product.stock_qty
        if before > Decimal('999999999.999') - movement.quantity:
            raise serializers.ValidationError('Невозможно восстановить остаток: превышен допустимый предел.')
        Product.objects.filter(pk=product.pk).update(
            stock_qty=before + movement.quantity,
            updated_at=timezone.now(),
        )
        now = timezone.now()
        movement.reversed_at = now
        movement.reversed_by = request.user
        movement.save(update_fields=['reversed_at', 'reversed_by'])
        StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.MovementType.WRITE_OFF_REVERSAL,
            quantity=movement.quantity,
            cost_price=movement.cost_price,
            writeoff_reason=movement.writeoff_reason,
            comment=f'Отмена списания №{movement.pk}'[:255],
            created_by=request.user,
            reversal_of=movement,
        )
        after = before + movement.quantity
        AuditLog.log(
            request,
            AuditLog.ActionType.STOCK_ADJUST,
            f'Отменено списание №{movement.pk}: «{product.name}» — восстановлено '
            f'{movement.quantity} {product.unit} (было {before}, стало {after}).',
        )
        return {'success': True, 'replayed': False, 'movement': movement, 'stock_qty': after}
