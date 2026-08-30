from django.db import models
from django.conf import settings
from decimal import Decimal

class Category(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название категории")
    slug = models.SlugField(max_length=100, unique=True, verbose_name="Слаг")
    icon = models.CharField(max_length=50, default="fa-tags", help_text="FontAwesome иконка", verbose_name="Иконка")
    description = models.TextField(blank=True, verbose_name="Описание")

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        ordering = ['name']

    def __str__(self):
        return self.name


class Brand(models.Model):
    name = models.CharField(max_length=100, verbose_name="Бренд")
    slug = models.SlugField(max_length=100, unique=True, verbose_name="Слаг")
    country = models.CharField(max_length=50, blank=True, verbose_name="Страна")

    class Meta:
        verbose_name = "Бренд"
        verbose_name_plural = "Бренды"
        ordering = ['name']

    def __str__(self):
        return self.name


class Product(models.Model):
    UNIT_CHOICES = [
        ('шт', 'штук (шт)'),
        ('л', 'литров (л)'),
        ('мл', 'миллилитров (мл)'),
        ('кг', 'килограмм (кг)'),
        ('компл', 'комплект (компл)'),
    ]

    name = models.CharField(max_length=255, verbose_name="Наименование товара")
    sku = models.CharField(max_length=50, unique=True, blank=True, db_index=True, verbose_name="Артикул")
    barcode = models.CharField(max_length=50, unique=True, blank=True, db_index=True, verbose_name="Штрихкод")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='products', verbose_name="Категория")
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name='products', verbose_name="Бренд")
    
    # Financial fields using DecimalField
    purchase_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Закупочная цена (₸)")
    retail_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name="Розничная цена (₸)")
    
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES, default='шт', verbose_name="Единица измерения")
    stock_qty = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('0.000'), verbose_name="Остаток на складе")
    min_stock_alert = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal('5.000'), verbose_name="Минимальный остаток (Сигнал)")
    
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ['name']

    def save(self, *args, **kwargs):
        import random
        if not self.barcode:
            self.barcode = f"200{random.randint(100000000, 999999999)}"
        if not self.sku:
            base_code = self.barcode[-6:] if len(self.barcode) >= 6 else str(random.randint(100000, 999999))
            self.sku = f"DAC-{base_code}"
            counter = 1
            while Product.objects.filter(sku=self.sku).exclude(pk=self.pk).exists():
                self.sku = f"DAC-{base_code}-{counter}"
                counter += 1
        if not self.category_id:
            cat = Category.objects.first()
            if not cat:
                cat = Category.objects.create(name="Автохимия и Аксессуары", slug="autochem")
            self.category = cat
        super().save(*args, **kwargs)

    @property
    def is_low_stock(self):
        return self.stock_qty <= self.min_stock_alert

    @property
    def margin(self):
        if self.retail_price and self.purchase_price:
            return self.retail_price - self.purchase_price
        return Decimal('0.00')

    def __str__(self):
        return f"[{self.sku}] {self.name} - {self.retail_price} ₸"


class StockMovement(models.Model):
    class MovementType(models.TextChoices):
        IN = 'IN', 'Приход / Поступление'
        OUT = 'OUT', 'Списание / Потеря'
        ADJUSTMENT = 'ADJUSTMENT', 'Корректировка инвентаризации'
        SALE = 'SALE', 'Продажа'
        RETURN = 'RETURN', 'Возврат от покупателя'

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_movements', verbose_name="Товар")
    movement_type = models.CharField(max_length=20, choices=MovementType.choices, verbose_name="Тип операции")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, verbose_name="Количество")
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Себестоимость (₸)")
    comment = models.CharField(max_length=255, blank=True, verbose_name="Комментарий")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name="Сотрудник")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата и время")

    class Meta:
        verbose_name = "Движение товара"
        verbose_name_plural = "Движение товаров"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_movement_type_display()} - {self.product.name} ({self.quantity} {self.product.unit})"
