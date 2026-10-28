from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.db import transaction
from django.db.models import Count, Exists, OuterRef
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html

from catalog.models import Brand, Category, Product, StockMovement
from config.admin_ui import DacarAdminMixin, money, quantity, status_badge
from sales.models import SaleOrderItem


@admin.register(Category)
class CategoryAdmin(DacarAdminMixin, admin.ModelAdmin):
    admin_description = 'Разделы каталога. Категорию можно удалить только после проверки связанных товаров.'
    list_display = ('name', 'slug', 'icon', 'products_count')
    search_fields = ('name', 'slug', 'description')
    prepopulated_fields = {'slug': ('name',)}
    ordering = ('name',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_products_count=Count('products'))

    @admin.display(description='Товаров', ordering='_products_count')
    def products_count(self, obj):
        return obj._products_count


@admin.register(Brand)
class BrandAdmin(DacarAdminMixin, admin.ModelAdmin):
    admin_description = 'Бренды товаров и страна происхождения.'
    list_display = ('name', 'slug', 'country', 'products_count')
    search_fields = ('name', 'slug', 'country')
    prepopulated_fields = {'slug': ('name',)}
    ordering = ('name',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_products_count=Count('products'))

    @admin.display(description='Товаров', ordering='_products_count')
    def products_count(self, obj):
        return obj._products_count


@admin.register(Product)
class ProductAdmin(DacarAdminMixin, admin.ModelAdmin):
    admin_description = (
        'Управление каталогом, ценами и остатками. Проданные товары защищены историей — '
        'их можно снять с продажи, но нельзя удалить.'
    )
    list_display = (
        'thumbnail_preview', 'product_display', 'sku', 'barcode', 'category', 'brand',
        'retail_display', 'stock_display', 'active_display', 'row_actions',
    )
    list_display_links = ('product_display',)
    list_filter = ('is_active', 'category', 'brand', 'unit', 'created_at')
    search_fields = ('name', 'sku', 'barcode', 'brand__name', 'category__name')
    ordering = ('name',)
    actions = ('archive_selected_products', 'restore_selected_products', 'delete_unused_products')
    save_on_top = True

    fieldsets = (
        ('Основная информация', {'fields': ('name', 'category', 'brand', 'is_active')}),
        ('Фото товара', {
            'fields': ('image',),
            'description': 'Рекомендуется WebP 800×800 с ровными отступами.',
        }),
        ('Идентификация', {
            'fields': ('barcode', 'sku'),
            'description': 'Пустой штрихкод или артикул система сгенерирует автоматически.',
        }),
        ('Цена и единица измерения', {'fields': ('retail_price', 'purchase_price', 'unit')}),
        ('Складской учёт', {'fields': ('stock_qty', 'min_stock_alert')}),
    )

    def get_queryset(self, request):
        return (
            super().get_queryset(request)
            .select_related('category', 'brand')
            .annotate(_has_sales=Exists(SaleOrderItem.objects.filter(product_id=OuterRef('pk'))))
        )

    @admin.display(description='Фото')
    def thumbnail_preview(self, obj):
        if not obj.image:
            return format_html('<span class="dc-photo-empty" aria-label="Нет фото">◇</span>')
        return format_html('<img class="dc-product-thumb" src="{}" alt="">', obj.image.url)

    @admin.display(description='Товар', ordering='name')
    def product_display(self, obj):
        return format_html(
            '<span class="dc-product-name">{}</span><small class="dc-cell-note">{}</small>',
            obj.name,
            obj.get_unit_display(),
        )

    @admin.display(description='Розница', ordering='retail_price')
    def retail_display(self, obj):
        return format_html('<strong class="dc-money">{}</strong>', money(obj.retail_price))

    @admin.display(description='Остаток', ordering='stock_qty')
    def stock_display(self, obj):
        if obj.stock_qty <= 0:
            tone = 'danger'
        elif obj.is_low_stock:
            tone = 'warning'
        else:
            tone = 'success'
        return status_badge(quantity(obj.stock_qty, obj.unit), tone)

    @admin.display(description='Статус', ordering='is_active')
    def active_display(self, obj):
        return status_badge('В продаже', 'success') if obj.is_active else status_badge('Архив', 'neutral')

    @admin.display(description='Действия')
    def row_actions(self, obj):
        change_url = reverse('admin:catalog_product_change', args=[obj.pk])
        if obj._has_sales:
            return format_html(
                '<span class="dc-row-actions"><a class="dc-row-link" href="{}">Изменить</a>'
                '<span class="dc-history-lock" title="Товар присутствует в чеках">История защищена</span></span>',
                change_url,
            )
        delete_url = reverse('admin:catalog_product_delete', args=[obj.pk])
        return format_html(
            '<span class="dc-row-actions"><a class="dc-row-link" href="{}">Изменить</a>'
            '<a class="dc-row-link dc-row-link-danger" href="{}">Удалить</a></span>',
            change_url,
            delete_url,
        )

    @admin.action(description='Снять с продажи — история сохранится')
    def archive_selected_products(self, request, queryset):
        updated = queryset.filter(is_active=True).update(is_active=False)
        self.message_user(request, f'Снято с продажи: {updated}.', messages.SUCCESS)

    @admin.action(description='Вернуть в продажу')
    def restore_selected_products(self, request, queryset):
        updated = queryset.filter(is_active=False).update(is_active=True)
        self.message_user(request, f'Возвращено в продажу: {updated}.', messages.SUCCESS)

    @admin.action(description='Удалить только товары без продаж')
    def delete_unused_products(self, request, queryset):
        allowed_ids = list(
            queryset.filter(sale_items__isnull=True).values_list('pk', flat=True).distinct()
        )
        allowed_count = len(allowed_ids)
        protected_count = queryset.count() - allowed_count
        if 'confirm' not in request.POST:
            return TemplateResponse(
                request,
                'admin/catalog/product/delete_unused_confirmation.html',
                {
                    **self.admin_site.each_context(request),
                    'title': 'Удаление товаров без продаж',
                    'opts': self.model._meta,
                    'queryset': queryset,
                    'allowed_count': allowed_count,
                    'protected_count': protected_count,
                    'action_checkbox_name': ACTION_CHECKBOX_NAME,
                },
            )
        with transaction.atomic():
            Product.objects.filter(pk__in=allowed_ids).delete()
        if allowed_count:
            self.message_user(request, f'Удалено товаров без истории продаж: {allowed_count}.', messages.SUCCESS)
        if protected_count:
            self.message_user(
                request,
                f'Не удалено товаров с историей продаж: {protected_count}. Их можно снять с продажи.',
                messages.WARNING,
            )

    def has_delete_permission(self, request, obj=None):
        allowed = super().has_delete_permission(request, obj)
        if obj is None:
            return allowed
        has_sales = getattr(obj, '_has_sales', None)
        if has_sales is None:
            has_sales = obj.sale_items.exists()
        return allowed and not has_sales

    def get_deleted_objects(self, objs, request):
        deleted_objects, model_count, perms_needed, protected = super().get_deleted_objects(objs, request)
        # An unused product and its stock records may be removed together.
        # Receipt history is protected by has_delete_permission above.
        if all(not obj.sale_items.exists() for obj in objs):
            perms_needed = set()
        return deleted_objects, model_count, perms_needed, protected

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop('delete_selected', None)
        return actions


@admin.register(StockMovement)
class StockMovementAdmin(DacarAdminMixin, admin.ModelAdmin):
    admin_description = 'Неизменяемая история прихода, списаний, продаж и возвратов.'
    list_display = (
        'created_at', 'product', 'movement_display', 'quantity_display',
        'cost_display', 'created_by', 'comment',
    )
    list_filter = ('movement_type', 'writeoff_reason', 'created_at', 'created_by')
    search_fields = ('product__name', 'product__sku', 'product__barcode', 'comment', 'created_by__username')
    ordering = ('-created_at',)
    actions = None
    readonly_fields = (
        'product', 'movement_type', 'quantity', 'cost_price', 'writeoff_reason', 'comment',
        'client_sync_id', 'created_by', 'created_at', 'reversed_at', 'reversed_by', 'reversal_of',
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('product', 'created_by')

    @admin.display(description='Операция', ordering='movement_type')
    def movement_display(self, obj):
        tones = {
            StockMovement.MovementType.IN: 'success',
            StockMovement.MovementType.RETURN: 'success',
            StockMovement.MovementType.SALE: 'info',
            StockMovement.MovementType.OUT: 'danger',
            StockMovement.MovementType.TRANSFER_TO_SHOP: 'warning',
        }
        return status_badge(obj.get_movement_type_display(), tones.get(obj.movement_type, 'neutral'))

    @admin.display(description='Количество', ordering='quantity')
    def quantity_display(self, obj):
        unit = obj.product.unit if obj.product_id else 'шт'
        return quantity(obj.quantity, unit)

    @admin.display(description='Себестоимость', ordering='cost_price')
    def cost_display(self, obj):
        return money(obj.cost_price)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
