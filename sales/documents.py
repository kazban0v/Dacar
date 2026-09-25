"""Read-only weekly settlement data and independently numbered company invoices."""
from datetime import datetime, time, timedelta
from decimal import Decimal, ROUND_DOWN

from django.db.models import Q, Prefetch
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from catalog.models import Product
from sales.models import CompanyInvoice, SaleOrder, SaleOrderItem
from sales.operations import financial_transaction, fingerprint, money, OperationConflict

SELLER = {
    'name': 'ИП DAKAR.RKZ.AQTOBE',
    'address': 'Актобе Г.А., Актобе, МИКРОРАЙОН 12, дом 45Д, кв/офис 97',
    'bin': '050810551773', 'bank': 'АО "Kaspi Bank"', 'kbe': '19',
    'bik': 'CASPKZKA', 'account': 'KZ48722S000056616714', 'tax': 'Без НДС',
}


def allocate_amount(total, weights):
    """Largest-remainder allocation in cents; result sums exactly to the order total."""
    denominator = sum(weights, Decimal('0'))
    if not denominator:
        if total:
            raise ValueError('Невозможно распределить сумму чека без стоимости позиций.')
        return [Decimal('0.00') for _ in weights]
    cents = int(total * 100)
    raw = [Decimal(cents) * value / denominator for value in weights]
    floors = [int(value.to_integral_value(rounding=ROUND_DOWN)) for value in raw]
    remainder = cents - sum(floors)
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - floors[i], reverse=True)[:remainder]:
        floors[i] += 1
    return [Decimal(value) / 100 for value in floors]


def is_shine(name):
    return ''.join(c for c in name.casefold() if c.isalnum()) in {'shinesystem', 'shinesystems'}


def weekly_shine_report(monday):
    if monday.weekday() != 0:
        raise ValueError('Выберите понедельник начала недели.')
    end_day = monday + timedelta(days=7)
    start = timezone.make_aware(datetime.combine(monday, time.min))
    end = timezone.make_aware(datetime.combine(end_day, time.min))
    orders = SaleOrder.objects.filter(status__in=['COMPLETED', 'REFUNDED']).filter(
        Q(created_at__gte=start, created_at__lt=end) |
        Q(status='REFUNDED', refunded_at__gte=start, refunded_at__lt=end)
    ).prefetch_related(Prefetch('items', queryset=SaleOrderItem.objects.select_related('product__brand').order_by('pk')))
    rows = {}
    events = []
    warnings = []
    for order in orders:
        items = list(order.items.all())
        if any(item.quantity <= 0 or item.total_amount < 0 for item in items) or order.total_amount < 0:
            raise ValueError(f'Чек {order.order_number}: некорректные старые суммы, нужна проверка.')
        amounts = allocate_amount(order.total_amount, [item.total_amount for item in items])
        for item, amount in zip(items, amounts):
            brand = item.brand_name_snapshot
            if not item.product_name_snapshot and item.product:
                brand = item.product.brand.name if item.product.brand else ''
            if not item.product_name_snapshot and not item.product:
                warnings.append(f'{order.order_number}: удалённый товар без сохранённого бренда не включён.')
            if not is_shine(brand):
                continue
            name = item.display_name
            sku = item.sku_snapshot or (item.product.sku if item.product else '')
            unit = item.unit_snapshot or (item.product.unit if item.product else 'шт')
            key = (sku, name, unit)
            row = rows.setdefault(key, dict(name=name, sku=sku, unit=unit,
                sold_qty=Decimal('0'), returned_qty=Decimal('0'),
                sold_amount=Decimal('0'), returned_amount=Decimal('0')))
            for returned, date in [(False, order.created_at), (True, order.refunded_at)]:
                if date is None or not start <= date < end or (returned and order.status != 'REFUNDED'):
                    continue
                prefix = 'returned' if returned else 'sold'
                row[prefix + '_qty'] += item.quantity
                row[prefix + '_amount'] += amount
                events.append(dict(order=order.order_number, date=date, name=name,
                    kind='Возврат' if returned else 'Продажа', quantity=item.quantity,
                    amount=-amount if returned else amount))
            if order.status == 'REFUNDED' and order.refunded_at is None:
                warnings.append(f'{order.order_number}: дата возврата отсутствует; возврат не учтён.')
    result = sorted(rows.values(), key=lambda row: (row['name'], row['sku']))
    for row in result:
        row['net_qty'] = row['sold_qty'] - row['returned_qty']
        row['net_amount'] = row['sold_amount'] - row['returned_amount']
    return dict(start=monday, end=end_day, rows=result,
        events=sorted(events, key=lambda row: (row['date'], row['order'])),
        sold_total=sum((row['sold_amount'] for row in result), Decimal('0')),
        returned_total=sum((row['returned_amount'] for row in result), Decimal('0')),
        net_total=sum((row['net_amount'] for row in result), Decimal('0')),
        warnings=sorted(set(warnings)))


def create_invoice(user, data):
    """Idempotent local document creation; no sale, payment or stock mutation."""
    digest = fingerprint(data)
    with financial_transaction():
        existing = CompanyInvoice.objects.filter(key=data['key']).first()
        if existing:
            if existing.creator_id != user.pk or existing.fingerprint != digest:
                raise OperationConflict('Этот номер запроса уже использован для другого счёта.')
            return existing
        lines = []
        for row in data['items']:
            product = Product.objects.filter(pk=row['product_id'], is_active=True).first()
            if product is None:
                raise ValidationError('Товар не найден или больше не активен.')
            price = money(product.retail_price)
            line_total = money(price * row['quantity'])
            if row['discount'] > line_total:
                raise ValidationError('Скидка больше стоимости позиции.')
            lines.append(dict(name=product.name, sku=product.sku, unit=product.unit,
                quantity=str(row['quantity']), price=str(price), discount=str(row['discount']),
                amount=str(line_total - row['discount'])))
        weights = [Decimal(line['amount']) for line in lines]
        total = money(sum(weights) - data['discount_amount'])
        for line, amount in zip(lines, allocate_amount(total, weights)):
            line['amount'] = str(amount)
        return CompanyInvoice.objects.create(key=data['key'], fingerprint=digest, creator=user,
            buyer_name=data['buyer_name'], buyer_bin=data['buyer_bin'],
            buyer_address=data['buyer_address'], contract=data['contract'],
            seller=SELLER, lines=lines, total_amount=total)
