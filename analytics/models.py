from django.db import models
from django.conf import settings

class AuditLog(models.Model):
    class ActionType(models.TextChoices):
        SALE = 'SALE', 'Продажа'
        REFUND = 'REFUND', 'Возврат'
        STOCK_IN = 'STOCK_IN', 'Поступление на склад'
        STOCK_OUT = 'STOCK_OUT', 'Списание со склада'
        STOCK_ADJUST = 'STOCK_ADJUST', 'Корректировка склада'
        PRODUCT_CREATE = 'PRODUCT_CREATE', 'Создание товара'
        PRODUCT_UPDATE = 'PRODUCT_UPDATE', 'Изменение товара'
        USER_ACTION = 'USER_ACTION', 'Действие пользователя'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name="Сотрудник")
    action_type = models.CharField(max_length=30, choices=ActionType.choices, verbose_name="Тип действия")
    description = models.TextField(verbose_name="Описание действия")
    ip_address = models.CharField(max_length=50, blank=True, verbose_name="IP Адрес")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата и время")

    class Meta:
        verbose_name = "Запись аудита"
        verbose_name_plural = "Журнал аудита (Audit Log)"
        ordering = ['-created_at']

    @classmethod
    def log(cls, request, action_type, description):
        user = request.user if request and hasattr(request, 'user') and request.user.is_authenticated else None
        ip = ''
        if request:
            # When behind Nginx reverse proxy, client IP is in X-Forwarded-For or X-Real-IP
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                ip = x_forwarded_for.split(',')[0].strip()
            elif request.META.get('HTTP_X_REAL_IP'):
                ip = request.META.get('HTTP_X_REAL_IP').strip()
            else:
                ip = request.META.get('REMOTE_ADDR', '')
        return cls.objects.create(
            user=user,
            action_type=action_type,
            description=description,
            ip_address=ip
        )

    def __str__(self):
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M')}] {self.user} - {self.get_action_type_display()}"
