from decimal import Decimal
import uuid
from rest_framework import serializers
from sales.models import SaleOrder, SaleOrderItem, SalePayment


class PaymentPartSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=['CASH', 'CARD', 'TRANSFER'])
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))


class SaleOrderItemCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField(min_value=1)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal('0.001'))
    discount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0,
        required=False, default=Decimal('0.00'))


class SaleCheckoutSerializer(serializers.Serializer):
    client_sync_id = serializers.RegexField(r'^[A-Za-z0-9_-]{16,64}$', required=False,
        default=lambda: uuid.uuid4().hex)
    items = SaleOrderItemCreateSerializer(many=True, max_length=200)
    payment_method = serializers.ChoiceField(choices=SaleOrder.PaymentMethod.choices)
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0,
        required=False, default=Decimal('0.00'))
    notes = serializers.CharField(required=False, allow_blank=True, default='', max_length=2000)
    payments = PaymentPartSerializer(many=True, required=False, max_length=3)

    def validate(self, attrs):
        parts = attrs.get('payments', [])
        if attrs['payment_method'] == 'MIXED':
            if len(parts) < 2 or len({part['method'] for part in parts}) != len(parts):
                raise serializers.ValidationError('Укажите минимум два разных способа оплаты.')
        elif parts:
            raise serializers.ValidationError('Разбивка доступна только для смешанной оплаты.')
        return attrs

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError('Корзина не может быть пустой.')
        ids = [row['product_id'] for row in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError('Объедините одинаковый товар в одну позицию.')
        return value

    def create(self, validated_data):
        from sales.operations import checkout
        order, self.replayed = checkout(self.context['request'], validated_data)
        return order

class SaleOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.ReadOnlyField(source='display_name')
    sku = serializers.ReadOnlyField(source='product.sku')

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        if request is not None and not request.user.is_admin_user:
            fields.pop('purchase_price_snapshot', None)
        return fields

    class Meta:
        model = SaleOrderItem
        fields = [
            'id', 'product', 'product_name', 'sku', 'quantity',
            'purchase_price_snapshot', 'unit_price', 'discount_amount', 'total_amount'
        ]


class SalePaymentSerializer(serializers.ModelSerializer):
    label = serializers.ReadOnlyField(source='get_method_display')

    class Meta:
        model = SalePayment
        fields = ['method', 'label', 'amount']


class SaleOrderSerializer(serializers.ModelSerializer):
    payments = SalePaymentSerializer(many=True, read_only=True)
    cashier_name = serializers.ReadOnlyField(source='cashier.get_full_name')
    payment_method_display = serializers.ReadOnlyField(source='get_payment_method_display')
    items = SaleOrderItemSerializer(many=True, read_only=True)
    gross_profit = serializers.ReadOnlyField()

    def get_fields(self):
        fields = super().get_fields()
        request = self.context.get('request')
        if request is not None and not request.user.is_admin_user:
            fields.pop('gross_profit', None)
        return fields

    class Meta:
        model = SaleOrder
        fields = [
            'id', 'order_number', 'cashier', 'cashier_name', 'status',
            'payment_method', 'payment_method_display', 'subtotal_amount', 'discount_amount', 'total_amount',
            'paid_amount', 'change_amount', 'gross_profit', 'notes',
            'created_at', 'items', 'payments'
        ]
