from rest_framework import serializers
from sales.models import SaleOrder, SaleOrderItem
from catalog.models import Product, StockMovement
from analytics.models import AuditLog
from django.db import transaction
from decimal import Decimal, InvalidOperation

class SaleOrderItemCreateSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    discount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0.00'))

class SaleCheckoutSerializer(serializers.Serializer):
    items = SaleOrderItemCreateSerializer(many=True)
    payment_method = serializers.ChoiceField(choices=SaleOrder.PaymentMethod.choices)
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False, default=Decimal('0.00'))
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("Корзина не может быть пустой.")
        return value

    def create(self, validated_data):
        request = self.context.get('request')
        cashier = request.user if request and request.user.is_authenticated else None

        items_data = validated_data['items']
        payment_method = validated_data['payment_method']
        paid_amount = validated_data['paid_amount']
        discount_total = validated_data.get('discount_amount', Decimal('0.00'))
        notes = validated_data.get('notes', '')

        with transaction.atomic():
            order_number = SaleOrder.generate_order_number()
            
            calculated_subtotal = Decimal('0.00')
            prepared_items = []

            for item_data in items_data:
                product_id = item_data['product_id']
                qty = Decimal(str(item_data['quantity']))
                item_discount = Decimal(str(item_data.get('discount', 0)))

                if qty <= Decimal('0.000'):
                    raise serializers.ValidationError(f"Количество товара должно быть больше 0.")

                # Lock product record for update to prevent concurrent stock overdraft
                try:
                    product = Product.objects.select_for_update().get(id=product_id, is_active=True)
                except Product.DoesNotExist:
                    raise serializers.ValidationError(f"Товар с ID {product_id} не найден или деактивирован.")

                if product.stock_qty < qty:
                    raise serializers.ValidationError(
                        f"Недостаточно товара '{product.name}' на складе. Доступно: {product.stock_qty} {product.unit}, запрошено: {qty} {product.unit}."
                    )

                # STRICT SERVER-SIDE CALCULATION: Fetch exact retail price from DB
                unit_price = product.retail_price
                purchase_price_snap = product.purchase_price
                item_subtotal = qty * unit_price
                item_total = max(Decimal('0.00'), item_subtotal - item_discount)

                calculated_subtotal += item_subtotal

                # Deduct inventory stock
                product.stock_qty -= qty
                product.save(update_fields=['stock_qty', 'updated_at'])

                # Log stock movement
                StockMovement.objects.create(
                    product=product,
                    movement_type=StockMovement.MovementType.SALE,
                    quantity=-qty,
                    cost_price=purchase_price_snap,
                    comment=f"Продажа по чеку {order_number}",
                    created_by=cashier
                )

                prepared_items.append({
                    'product': product,
                    'quantity': qty,
                    'purchase_price_snapshot': purchase_price_snap,
                    'unit_price': unit_price,
                    'discount_amount': item_discount,
                    'total_amount': item_total,
                })

            final_total = max(Decimal('0.00'), calculated_subtotal - discount_total)
            
            if paid_amount < final_total and payment_method != SaleOrder.PaymentMethod.MIXED:
                # Allow exact or overpaid, or handle change
                raise serializers.ValidationError(
                    f"Внесенная сумма ({paid_amount} ₸) меньше итоговой суммы к оплате ({final_total} ₸)."
                )

            change_amount = max(Decimal('0.00'), paid_amount - final_total)

            sale_order = SaleOrder.objects.create(
                order_number=order_number,
                cashier=cashier,
                status=SaleOrder.Status.COMPLETED,
                payment_method=payment_method,
                subtotal_amount=calculated_subtotal,
                discount_amount=discount_total,
                total_amount=final_total,
                paid_amount=paid_amount,
                change_amount=change_amount,
                notes=notes
            )

            for item in prepared_items:
                SaleOrderItem.objects.create(
                    order=sale_order,
                    product=item['product'],
                    quantity=item['quantity'],
                    purchase_price_snapshot=item['purchase_price_snapshot'],
                    unit_price=item['unit_price'],
                    discount_amount=item['discount_amount'],
                    total_amount=item['total_amount']
                )

            AuditLog.log(
                request,
                AuditLog.ActionType.SALE,
                f"Проведен чек № {sale_order.order_number} на сумму {sale_order.total_amount:.0f} ₸ (Способ оплаты: {sale_order.get_payment_method_display()})"
            )

            return sale_order


class SaleOrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.ReadOnlyField(source='product.name')
    sku = serializers.ReadOnlyField(source='product.sku')

    class Meta:
        model = SaleOrderItem
        fields = [
            'id', 'product', 'product_name', 'sku', 'quantity',
            'purchase_price_snapshot', 'unit_price', 'discount_amount', 'total_amount'
        ]


class SaleOrderSerializer(serializers.ModelSerializer):
    cashier_name = serializers.ReadOnlyField(source='cashier.get_full_name')
    payment_method_display = serializers.ReadOnlyField(source='get_payment_method_display')
    items = SaleOrderItemSerializer(many=True, read_only=True)
    gross_profit = serializers.ReadOnlyField()

    class Meta:
        model = SaleOrder
        fields = [
            'id', 'order_number', 'cashier', 'cashier_name', 'status',
            'payment_method', 'payment_method_display', 'subtotal_amount', 'discount_amount', 'total_amount',
            'paid_amount', 'change_amount', 'gross_profit', 'notes',
            'created_at', 'items'
        ]
