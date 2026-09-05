from django.db import models
from django.conf import settings
from catalog.models import Product
from decimal import Decimal
import uuid
import datetime

class SaleOrder(models.Model):
    class Status(models.TextChoices):
        COMPLETED = 'COMPLETED', 'Проведен (Оплачен)'
        REFUNDED = 'REFUNDED', 'Возвращен'
        CANCELLED = 'CANCELLED', 'Отменен'

    class PaymentMethod(models.TextChoices):
        CASH = 'CASH', 'Наличные'
        CARD = 'CARD', 'Карта'
        TRANSFER = 'TRANSFER', 'Kaspi QR'
        MIXED = 'MIXED', 'Смешанная'

    order_number = models.CharField(max_length=50, unique=True, db_index=True, verbose_name="Номер чека")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales', verbose_name="Кассир")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED, verbose_name="Статус")
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH, verbose_name="Способ оплаты")

    # Financial breakdown
    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Сумма без скидки (₸)")
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Скидка (₸)")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Итого к оплате (₸)")
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Внесено (₸)")
    change_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Сдача (₸)")
    
    notes = models.TextField(blank=True, verbose_name="Заметки")
    
    # Refund audit fields
    refund_reason = models.CharField(max_length=255, blank=True, verbose_name="Причина возврата")
    refunded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='refunds_processed', verbose_name="Провел возврат")
    refunded_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата и время возврата")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата и время")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата изменения")

    class Meta:
        verbose_name = "Продажа (Чек)"
        verbose_name_plural = "Продажи (Чеки)"
        ordering = ['-created_at']

    @classmethod
    def generate_order_number(cls):
        from django.utils import timezone
        today_str = timezone.localdate().strftime('%Y%m%d')
        prefix = f"DACAR-{today_str}-"
        last_order = cls.objects.filter(order_number__startswith=prefix).order_by('-id').first()
        if last_order:
            try:
                seq = int(last_order.order_number.split('-')[-1]) + 1
            except ValueError:
                seq = 1
        else:
            seq = 1
        return f"{prefix}{seq:04d}"

    @property
    def gross_profit(self):
        """Calculates total gross profit for this sale order."""
        total_cost = sum(item.quantity * item.purchase_price_snapshot for item in self.items.all())
        return self.total_amount - total_cost

    def __str__(self):
        return f"Чек № {self.order_number} ({self.total_amount} ₸)"


class SaleOrderItem(models.Model):
    order = models.ForeignKey(SaleOrder, on_delete=models.CASCADE, related_name='items', verbose_name="Чек")
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_items', verbose_name="Товар")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, verbose_name="Количество")
    
    # Financial snapshot at time of checkout
    purchase_price_snapshot = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Закупочная цена (₸)")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Розница за ед. (₸)")
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Скидка на позицию (₸)")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Итого за позицию (₸)")

    class Meta:
        verbose_name = "Позиция чека"
        verbose_name_plural = "Позиции чека"

    @property
    def item_profit(self):
        return self.total_amount - (self.quantity * self.purchase_price_snapshot)

    def __str__(self):
        product_name = self.product.name if self.product else "Удалённый товар"
        return f"{product_name} x {self.quantity} = {self.total_amount} ₸"
