from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from analytics.models import AuditLog
from config.admin_ui import DacarAdminMixin, status_badge


@admin.register(AuditLog)
class AuditLogAdmin(DacarAdminMixin, admin.ModelAdmin):
    admin_description = 'Неизменяемая история критичных действий сотрудников и системы.'
    list_display = ('created_at', 'user', 'action_display', 'description_display', 'details_action')
    list_filter = ('action_type', 'created_at', 'user')
    search_fields = ('description', 'user__username', 'user__first_name', 'ip_address')
    ordering = ('-created_at',)
    actions = None
    readonly_fields = ('user', 'action_type', 'description', 'metadata', 'ip_address', 'created_at')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')

    @admin.display(description='Событие', ordering='action_type')
    def action_display(self, obj):
        tones = {
            AuditLog.ActionType.SALE: 'success',
            AuditLog.ActionType.REFUND: 'danger',
            AuditLog.ActionType.STOCK_IN: 'info',
            AuditLog.ActionType.STOCK_OUT: 'warning',
            AuditLog.ActionType.STOCK_ADJUST: 'warning',
            AuditLog.ActionType.PRODUCT_CREATE: 'success',
            AuditLog.ActionType.PRODUCT_UPDATE: 'info',
        }
        return status_badge(obj.get_action_type_display(), tones.get(obj.action_type, 'neutral'))

    @admin.display(description='Описание')
    def description_display(self, obj):
        return format_html('<span class="dc-audit-description">{}</span>', obj.description)

    @admin.display(description='Действия')
    def details_action(self, obj):
        url = reverse('admin:analytics_auditlog_change', args=[obj.pk])
        return format_html('<a class="dc-row-link" href="{}">Подробнее</a>', url)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
