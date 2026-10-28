from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils.html import format_html

from config.admin_ui import DacarAdminMixin, money, status_badge
from users.models import CashShift, User
from users.permissions import can_manage_staff


@admin.register(User)
class UserAdmin(DacarAdminMixin, BaseUserAdmin):
    admin_description = 'Учётные записи администраторов и кассиров. Блокировка сохраняет историю сотрудника.'
    list_display = ('employee_display', 'role_display', 'phone', 'last_login', 'status_display', 'row_actions')
    list_filter = ('role', 'is_active', 'is_staff', 'is_superuser', 'date_joined')
    search_fields = ('username', 'first_name', 'last_name', 'phone', 'email')
    ordering = ('-date_joined',)

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Персональные данные', {'fields': ('first_name', 'last_name', 'email', 'phone')}),
        ('Роль и доступ', {
            'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
            'description': 'Отключите «Активен», чтобы заблокировать вход без удаления истории.',
        }),
        ('Системная информация', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'username', 'password1', 'password2', 'first_name', 'last_name',
                'phone', 'role', 'is_staff', 'is_superuser',
            ),
        }),
    )

    @admin.display(description='Сотрудник', ordering='username')
    def employee_display(self, obj):
        return format_html(
            '<span class="dc-product-name">{}</span><small class="dc-cell-note">@{}</small>',
            obj.get_full_name() or obj.username,
            obj.username,
        )

    @admin.display(description='Роль', ordering='role')
    def role_display(self, obj):
        return status_badge(obj.get_role_display(), 'info' if obj.is_admin_user else 'neutral')

    @admin.display(description='Доступ', ordering='is_active')
    def status_display(self, obj):
        return status_badge('Активен', 'success') if obj.is_active else status_badge('Заблокирован', 'danger')

    @admin.display(description='Действия')
    def row_actions(self, obj):
        url = reverse('admin:users_user_change', args=[obj.pk])
        return format_html('<a class="dc-row-link" href="{}">Управлять</a>', url)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            fields += ['is_staff', 'is_superuser', 'groups', 'user_permissions']
            if obj and obj.pk == request.user.pk:
                fields += ['role', 'is_active']
        return fields

    def has_change_permission(self, request, obj=None):
        allowed = super().has_change_permission(request, obj)
        if obj and not request.user.is_superuser and obj.pk != request.user.pk:
            return allowed and can_manage_staff(request.user, obj)
        return allowed

    def has_delete_permission(self, request, obj=None):
        allowed = super().has_delete_permission(request, obj)
        return allowed and (obj is None or can_manage_staff(request.user, obj))

    def delete_queryset(self, request, queryset):
        if any(not can_manage_staff(request.user, target) for target in queryset):
            raise PermissionDenied
        super().delete_queryset(request, queryset)


@admin.register(CashShift)
class CashShiftAdmin(DacarAdminMixin, admin.ModelAdmin):
    admin_description = 'Архив кассовых смен. Смена привязана к чекам и доступна только для просмотра.'
    list_display = ('id_display', 'cashier', 'status_display', 'opened_at', 'closed_at', 'counted_cash_display')
    list_filter = ('status', 'cashier', 'opened_at')
    search_fields = ('cashier__username', 'cashier__first_name', 'cashier__last_name')
    readonly_fields = (
        'cashier', 'status', 'opened_at', 'closed_at', 'opening_cash', 'counted_cash',
        'opening_note', 'closing_note',
    )
    ordering = ('-opened_at',)
    actions = None

    @admin.display(description='Смена', ordering='id')
    def id_display(self, obj):
        return f'#{obj.pk}'

    @admin.display(description='Статус', ordering='status')
    def status_display(self, obj):
        return status_badge(
            obj.get_status_display(),
            'success' if obj.status == CashShift.Status.OPEN else 'neutral',
        )

    @admin.display(description='Наличные при закрытии', ordering='counted_cash')
    def counted_cash_display(self, obj):
        return money(obj.counted_cash)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
