from django.contrib import admin
from sales.models import SaleOrder, SaleOrderItem

class SaleOrderItemInline(admin.TabularInline):
    model = SaleOrderItem
    extra = 0
    fields = ('product', 'quantity', 'unit_price', 'purchase_price_snapshot', 'discount_amount', 'total_amount')
    readonly_fields = ('total_amount',)


@admin.register(SaleOrder)
class SaleOrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_number', 'cashier', 'total_amount', 'discount_amount',
        'paid_amount', 'change_amount', 'payment_method', 'status', 'created_at'
    )
    list_filter = ('status', 'payment_method', 'created_at', 'cashier')
    search_fields = ('order_number', 'cashier__username', 'cashier__first_name', 'cashier__last_name', 'notes', 'refund_reason')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    inlines = [SaleOrderItemInline]


@admin.register(SaleOrderItem)
class SaleOrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product', 'quantity', 'unit_price', 'discount_amount', 'total_amount')
    list_filter = ('order__created_at', 'order__payment_method')
    search_fields = ('order__order_number', 'product__name', 'product__sku', 'product__barcode')
