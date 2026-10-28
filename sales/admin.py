from django import forms
from django.contrib import admin
from django.db.models import Exists, OuterRef
from django.urls import reverse
from django.utils.html import format_html

from config.admin_ui import DacarAdminMixin, money, quantity, status_badge
from sales.gift_cards import code_digest, normalize_code
from sales.models import (
    CompanyInvoice,
    GiftCard,
    GiftCardEvent,
    SaleOrder,
    SaleOrderItem,
    SalePayment,
)


class GiftCardAdminForm(forms.ModelForm):
    barcode = forms.CharField(
        label='Штрихкод сертификата',
        help_text='Формат DKR-XXXXX-XXXXX. После сохранения полный код не отображается.',
        required=False,
    )

    class Meta:
        model = GiftCard
        fields = ('barcode', 'nominal', 'is_blocked')

    def clean_barcode(self):
        if self.instance.pk:
            return ''
        if not self.cleaned_data.get('barcode'):
            raise forms.ValidationError('Введите штрихкод сертификата.')
        try:
            barcode = normalize_code(self.cleaned_data['barcode'])
        except Exception as exc:
            detail = getattr(exc, 'detail', exc)
            if isinstance(detail, (list, tuple)):
                detail = detail[0]
            raise forms.ValidationError(str(detail)) from exc
        if GiftCard.objects.filter(code_hash=code_digest(barcode)).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Такой сертификат уже есть в реестре.')
        return barcode

    def save(self, commit=True):
        card = super().save(commit=False)
        if not card.pk:
            barcode = self.cleaned_data['barcode']
            card.code_hash = code_digest(barcode)
            card.code_last4 = barcode[-4:]
            card.balance = 0
        if commit:
            card.save()
        return card


class ReadOnlyInlineMixin:
    extra = 0
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SalePaymentInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = SalePayment
    fields = ('method', 'amount')
    readonly_fields = fields
    verbose_name = 'Оплата'
    verbose_name_plural = 'Разбивка оплаты'


class SaleOrderItemInline(ReadOnlyInlineMixin, admin.TabularInline):
    model = SaleOrderItem
    fields = ('product', 'product_name_snapshot', 'quantity', 'unit_price', 'discount_amount', 'total_amount')
    readonly_fields = fields
    verbose_name = 'Товар в чеке'
    verbose_name_plural = 'Состав чека'


class FinancialHistoryAdmin(DacarAdminMixin, admin.ModelAdmin):
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SaleOrder)
class SaleOrderAdmin(FinancialHistoryAdmin):
    admin_description = 'Чеки доступны для просмотра. Финансовая история не редактируется и не удаляется.'
    list_display = (
        'order_display', 'cashier', 'payment_display', 'total_display',
        'status_display', 'created_at', 'open_action',
    )
    list_display_links = ('order_display',)
    list_filter = ('status', 'payment_method', 'created_at', 'cashier')
    search_fields = (
        'order_number', 'cashier__username', 'cashier__first_name',
        'cashier__last_name', 'notes', 'refund_reason',
    )
    ordering = ('-created_at',)
    inlines = [SaleOrderItemInline, SalePaymentInline]
    readonly_fields = (
        'order_number', 'cashier', 'shift', 'refund_shift', 'status', 'payment_method',
        'subtotal_amount', 'discount_amount', 'surcharge_amount', 'total_amount',
        'paid_amount', 'change_amount',
        'notes', 'refund_reason', 'refunded_by', 'refunded_at', 'created_at', 'updated_at',
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('cashier')

    @admin.display(description='Чек', ordering='order_number')
    def order_display(self, obj):
        return format_html('<strong>{}</strong>', obj.order_number)

    @admin.display(description='Оплата', ordering='payment_method')
    def payment_display(self, obj):
        return status_badge(obj.get_payment_method_display(), 'info')

    @admin.display(description='Сумма', ordering='total_amount')
    def total_display(self, obj):
        return format_html('<strong class="dc-money">{}</strong>', money(obj.total_amount))

    @admin.display(description='Статус', ordering='status')
    def status_display(self, obj):
        tones = {
            SaleOrder.Status.COMPLETED: 'success',
            SaleOrder.Status.REFUNDED: 'warning',
            SaleOrder.Status.CANCELLED: 'danger',
        }
        return status_badge(obj.get_status_display(), tones.get(obj.status, 'neutral'))

    @admin.display(description='Действия')
    def open_action(self, obj):
        url = reverse('admin:sales_saleorder_change', args=[obj.pk])
        return format_html('<a class="dc-row-link" href="{}">Открыть чек</a>', url)


@admin.register(SaleOrderItem)
class SaleOrderItemAdmin(FinancialHistoryAdmin):
    admin_description = 'Снимки товарных позиций на момент проведения чека.'
    list_display = ('order', 'product_display', 'quantity_display', 'unit_price_display', 'total_display')
    list_filter = ('order__created_at', 'order__payment_method')
    search_fields = (
        'order__order_number', 'product__name', 'product__sku', 'product__barcode',
        'product_name_snapshot', 'sku_snapshot',
    )
    readonly_fields = (
        'order', 'product', 'quantity', 'product_name_snapshot', 'brand_name_snapshot',
        'sku_snapshot', 'unit_snapshot', 'purchase_price_snapshot', 'unit_price',
        'discount_amount', 'total_amount',
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('order', 'product')

    @admin.display(description='Товар')
    def product_display(self, obj):
        return obj.display_name

    @admin.display(description='Количество', ordering='quantity')
    def quantity_display(self, obj):
        return quantity(obj.quantity, obj.unit_snapshot or 'шт')

    @admin.display(description='Цена', ordering='unit_price')
    def unit_price_display(self, obj):
        return money(obj.unit_price)

    @admin.display(description='Итого', ordering='total_amount')
    def total_display(self, obj):
        return money(obj.total_amount)


@admin.register(GiftCard)
class GiftCardAdmin(DacarAdminMixin, admin.ModelAdmin):
    admin_description = (
        'Реестр физических подарочных сертификатов. Удалить можно только ошибочно добавленную '
        'карту, которую ещё не активировали.'
    )
    form = GiftCardAdminForm
    list_display = (
        'last4_display', 'nominal_display', 'balance_display', 'state_display',
        'activated_display', 'expires_display', 'actions_display',
    )
    list_display_links = ('last4_display',)
    list_filter = ('is_blocked', 'activated_at', 'expires_at')
    search_fields = ('code_last4',)
    ordering = ('-created_at',)
    actions = None

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _has_events=Exists(GiftCardEvent.objects.filter(card_id=OuterRef('pk')))
        )

    @admin.display(description='Карта', ordering='code_last4')
    def last4_display(self, obj):
        return format_html('<strong>•••• {}</strong>', obj.code_last4)

    @admin.display(description='Номинал', ordering='nominal')
    def nominal_display(self, obj):
        return money(obj.nominal)

    @admin.display(description='Остаток', ordering='balance')
    def balance_display(self, obj):
        return money(obj.balance)

    @admin.display(description='Статус')
    def state_display(self, obj):
        labels = {
            'UNISSUED': ('Не активирован', 'neutral'),
            'ACTIVE': ('Активен', 'success'),
            'SPENT': ('Использован', 'info'),
            'EXPIRED': ('Срок истёк', 'warning'),
            'BLOCKED': ('Заблокирован', 'danger'),
        }
        label, tone = labels[obj.state]
        return status_badge(label, tone)

    @admin.display(description='Активирован', ordering='activated_at')
    def activated_display(self, obj):
        return obj.activated_at or '—'

    @admin.display(description='Действует до', ordering='expires_at')
    def expires_display(self, obj):
        return obj.expires_at or '—'

    @admin.display(description='Действия')
    def actions_display(self, obj):
        change_url = reverse('admin:sales_giftcard_change', args=[obj.pk])
        has_events = getattr(obj, '_has_events', None)
        if has_events is None:
            has_events = obj.events.exists()
        if obj.activated_at is None and not has_events:
            delete_url = reverse('admin:sales_giftcard_delete', args=[obj.pk])
            return format_html(
                '<span class="dc-row-actions"><a class="dc-row-link" href="{}">Управлять</a>'
                '<a class="dc-row-link dc-row-link-danger" href="{}">Удалить</a></span>',
                change_url,
                delete_url,
            )
        return format_html(
            '<span class="dc-row-actions"><a class="dc-row-link" href="{}">Управлять</a>'
            '<span class="dc-history-lock">История защищена</span></span>',
            change_url,
        )

    def get_fields(self, request, obj=None):
        if obj is None:
            return ('barcode', 'nominal')
        return (
            'code_last4', 'nominal', 'balance', 'activated_at', 'expires_at',
            'activated_by', 'activation_payment_method', 'is_blocked', 'created_at',
        )

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return (
            'code_last4', 'nominal', 'balance', 'activated_at', 'expires_at',
            'activated_by', 'activation_payment_method', 'created_at',
        )

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return False
        has_events = getattr(obj, '_has_events', None)
        if has_events is None:
            has_events = obj.events.exists()
        return super().has_delete_permission(request, obj) and obj.activated_at is None and not has_events


@admin.register(GiftCardEvent)
class GiftCardEventAdmin(FinancialHistoryAdmin):
    admin_description = 'Неизменяемая история активаций, оплат и возвратов по сертификатам.'
    list_display = ('created_at', 'card', 'kind_display', 'amount_display', 'balance_display', 'order', 'actor')
    list_filter = ('kind', 'created_at')
    search_fields = ('card__code_last4', 'order__order_number', 'actor__username')
    ordering = ('-created_at', '-pk')
    readonly_fields = ('card', 'kind', 'amount', 'balance_after', 'order', 'actor', 'created_at')

    @admin.display(description='Событие', ordering='kind')
    def kind_display(self, obj):
        tones = {
            GiftCardEvent.Kind.ACTIVATE: 'success',
            GiftCardEvent.Kind.REDEEM: 'info',
            GiftCardEvent.Kind.REFUND: 'warning',
        }
        return status_badge(obj.get_kind_display(), tones.get(obj.kind, 'neutral'))

    @admin.display(description='Сумма', ordering='amount')
    def amount_display(self, obj):
        return money(obj.amount)

    @admin.display(description='Остаток', ordering='balance_after')
    def balance_display(self, obj):
        return money(obj.balance_after)


@admin.register(CompanyInvoice)
class CompanyInvoiceAdmin(FinancialHistoryAdmin):
    admin_description = 'Сформированные счета для компаний. Документы сохранены без возможности изменения.'
    list_display = ('number_display', 'buyer_name', 'buyer_bin', 'total_display', 'creator', 'created_at')
    list_filter = ('created_at', 'creator')
    search_fields = ('buyer_name', 'buyer_bin', 'buyer_address', 'creator__username')
    ordering = ('-created_at',)
    readonly_fields = (
        'key', 'fingerprint', 'creator', 'buyer_name', 'buyer_bin', 'buyer_address',
        'contract', 'seller', 'lines', 'total_amount', 'created_at',
    )

    @admin.display(description='Счёт')
    def number_display(self, obj):
        return obj.number

    @admin.display(description='Сумма', ordering='total_amount')
    def total_display(self, obj):
        return money(obj.total_amount)
