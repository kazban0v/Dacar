from django.contrib import admin
from analytics.models import AuditLog

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'action_type', 'description', 'ip_address')
    list_filter = ('action_type', 'created_at', 'user')
    search_fields = ('description', 'user__username', 'user__first_name', 'ip_address')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    
    # Audit log records are security events - make them readonly to prevent tampering
    readonly_fields = ('user', 'action_type', 'description', 'ip_address', 'created_at')

    def has_add_permission(self, request):
        return False  # Audit logs are generated programmatically only
