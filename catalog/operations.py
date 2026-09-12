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
    comment = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')

    def validate(self, data):
        if data['action'] != 'ADJUSTMENT' and data['quantity'] == 0:
            raise serializers.ValidationError('Количество должно быть больше нуля.')
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
        StockMovement.objects.create(product=product, movement_type=action, quantity=qty,
            cost_price=data['cost_price'] if data['cost_price'] is not None else product.purchase_price,
            comment=data['comment'], created_by=request.user, client_sync_id=data['client_sync_id'])
        audit_type = 'STOCK_ADJUST' if action == 'ADJUSTMENT' else ('STOCK_IN' if action == 'IN' else 'STOCK_OUT')
        AuditLog.log(request, audit_type,
            f'{label}: «{product.name}» — {qty} {product.unit} (было {before}, стало {after}). {data["comment"]}')
        result = {'success': True, 'message': f'{label}: «{product.name}» — выполнено.',
            'product_id': product.pk, 'product_name': product.name, 'stock_qty': float(after),
            'prev_stock': float(before), 'unit': product.unit, 'is_low_stock': after <= product.min_stock_alert}
        OperationReceipt.objects.create(key=data['client_sync_id'], actor=request.user,
            kind='stock', fingerprint=fingerprint(data), result=result)
        return {**result, 'replayed': False}
