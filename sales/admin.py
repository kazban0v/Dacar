from django.contrib import admin
from django import forms
from django.urls import reverse
from django.utils.html import format_html
from sales.models import SaleOrder, SaleOrderItem, SalePayment, GiftCard, GiftCardEvent
from sales.gift_cards import code_digest, normalize_code


class GiftCardAdminForm(forms.ModelForm):
    barcode = forms.CharField(
        label='Штрихкод сертификата',
        help_text='Формат DKR-XXXXX-XXXXX. Код хэшируется и после сохранения больше не показывается.',
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


class SalePaymentInline(admin.TabularInline):
    model = SalePayment
    extra = 0
    fields = ('method', 'amount')
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

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
    inlines = [SaleOrderItemInline, SalePaymentInline]


@admin.register(SaleOrderItem)
class SaleOrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product', 'quantity', 'unit_price', 'discount_amount', 'total_amount')
    list_filter = ('order__created_at', 'order__payment_method')
    search_fields = ('order__order_number', 'product__name', 'product__sku', 'product__barcode')


@admin.register(GiftCard)
class GiftCardAdmin(admin.ModelAdmin):
    form = GiftCardAdminForm
    list_display = ('last4_display', 'nominal_display', 'balance_display', 'state_display',
                    'activated_display', 'expires_display', 'blocked_display', 'actions_display')
    list_display_links = ('last4_display',)
    list_filter = ('is_blocked', 'activated_at')
    search_fields = ('code_last4',)
    ordering = ('-created_at',)

    @admin.display(description='Последние 4', ordering='code_last4')
    def last4_display(self, obj):
        return obj.code_last4

    @admin.display(description='Номинал', ordering='nominal')
    def nominal_display(self, obj):
        return obj.nominal

    @admin.display(description='Остаток', ordering='balance')
    def balance_display(self, obj):
        return obj.balance

    @admin.display(description='Статус')
    def state_display(self, obj):
        return {
            'UNISSUED': 'Не активирован',
            'ACTIVE': 'Активен',
            'SPENT': 'Использован',
            'EXPIRED': 'Срок истёк',
            'BLOCKED': 'Заблокирован',
        }[obj.state]

    @admin.display(description='Активирован', ordering='activated_at')
    def activated_display(self, obj):
        return obj.activated_at or '—'

    @admin.display(description='Действует до', ordering='expires_at')
    def expires_display(self, obj):
        return obj.expires_at or '—'

    @admin.display(description='Заблокирован', boolean=True, ordering='is_blocked')
    def blocked_display(self, obj):
        return obj.is_blocked

    @admin.display(description='Действия')
    def actions_display(self, obj):
        change_url = reverse('admin:sales_giftcard_change', args=[obj.pk])
        if obj.activated_at is None and not obj.events.exists():
            delete_url = reverse('admin:sales_giftcard_delete', args=[obj.pk])
            return format_html(
                '<a class="button" href="{}">Управлять</a> '
                '<a class="button" style="color:#b91c1c" href="{}">Удалить</a>',
                change_url, delete_url,
            )
        return format_html(
            '<a class="button" href="{}">Управлять</a> '
            '<span title="Активированные карты сохраняются вместе с чеками и аудитом">История защищена</span>',
            change_url,
        )

    def get_fields(self, request, obj=None):
        if obj is None:
            return ('barcode', 'nominal')
        return ('code_last4', 'nominal', 'balance', 'activated_at', 'expires_at',
                'activated_by', 'activation_payment_method', 'is_blocked', 'created_at')

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return ('code_last4', 'nominal', 'balance', 'activated_at', 'expires_at',
                'activated_by', 'activation_payment_method', 'created_at')

    def has_add_permission(self, request):
        return super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        # Keep financial history immutable. Only never-activated cards without
        # events may be removed (for example, a typo or local demo card).
        if obj is None:
            return False
        return (
            super().has_delete_permission(request, obj)
            and obj.activated_at is None
            and not obj.events.exists()
        )


@admin.register(GiftCardEvent)
class GiftCardEventAdmin(admin.ModelAdmin):
    list_display = ('card', 'kind', 'amount', 'balance_after', 'order', 'actor', 'created_at')
    list_filter = ('kind', 'created_at')
    readonly_fields = ('card', 'kind', 'amount', 'balance_after', 'order', 'actor', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
