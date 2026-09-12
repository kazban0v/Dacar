from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from users.models import User
from users.permissions import can_manage_staff
from django.core.exceptions import PermissionDenied

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'first_name', 'last_name', 'phone', 'role', 'is_active', 'is_staff', 'date_joined')
    list_filter = ('role', 'is_active', 'is_staff', 'is_superuser', 'date_joined')
    search_fields = ('username', 'first_name', 'last_name', 'phone', 'email')
    ordering = ('-date_joined',)

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Персональные данные', {'fields': ('first_name', 'last_name', 'email', 'phone')}),
        ('Роль и права доступа', {
            'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Важные даты', {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'password1', 'password2', 'first_name', 'last_name', 'phone', 'role', 'is_staff', 'is_superuser'),
        }),
    )

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
