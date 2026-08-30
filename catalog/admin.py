from django.contrib import admin
from catalog.models import Category, Brand, Product, StockMovement

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'icon', 'get_products_count')
    search_fields = ('name', 'slug', 'description')
    prepopulated_fields = {'slug': ('name',)}

    def get_products_count(self, obj):
        return obj.products.count()
    get_products_count.short_description = "Кол-во товаров"


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'country', 'get_products_count')
    search_fields = ('name', 'slug', 'country')
    prepopulated_fields = {'slug': ('name',)}

    def get_products_count(self, obj):
        return obj.products.count()
    get_products_count.short_description = "Кол-во товаров"


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'name', 'sku', 'barcode', 'category', 'brand',
        'retail_price', 'purchase_price', 'unit',
        'stock_qty', 'min_stock_alert', 'is_active', 'created_at'
    )
    list_filter = ('is_active', 'category', 'brand', 'unit', 'created_at')
    search_fields = ('name', 'sku', 'barcode', 'brand__name', 'category__name')
    list_editable = ('retail_price', 'purchase_price', 'stock_qty', 'is_active')
    ordering = ('name',)
    date_hierarchy = 'created_at'


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('product', 'movement_type', 'quantity', 'cost_price', 'created_by', 'comment', 'created_at')
    list_filter = ('movement_type', 'created_at', 'created_by')
    search_fields = ('product__name', 'product__sku', 'product__barcode', 'comment', 'created_by__username')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
