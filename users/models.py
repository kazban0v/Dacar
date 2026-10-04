from decimal import Decimal

from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Администратор'
        CASHIER = 'CASHIER', 'Кассир'

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.CASHIER,
        verbose_name="Роль"
    )
    phone = models.CharField(max_length=30, blank=True, null=True, verbose_name="Телефон")

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    @property
    def is_admin_user(self):
        return self.role == self.Role.ADMIN or self.is_superuser

    @property
    def is_cashier_user(self):
        return self.role == self.Role.CASHIER and not self.is_superuser

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"


class CashShift(models.Model):
    """A real cashier shift. Sales keep a link to the shift that posted them."""

    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Открыта'
        CLOSED = 'CLOSED', 'Закрыта'

    cashier = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='cash_shifts', verbose_name='Кассир'
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    opened_at = models.DateTimeField(default=timezone.now, verbose_name='Открыта')
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name='Закрыта')
    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name='Размен при открытии')
    counted_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Наличные при закрытии')
    opening_note = models.CharField(max_length=255, blank=True, verbose_name='Комментарий при открытии')
    closing_note = models.CharField(max_length=255, blank=True, verbose_name='Комментарий при закрытии')

    class Meta:
        verbose_name = 'Кассовая смена'
        verbose_name_plural = 'Кассовые смены'
        ordering = ['-opened_at']
        constraints = [
            models.UniqueConstraint(
                fields=['cashier'], condition=Q(status='OPEN'), name='one_open_cash_shift_per_cashier'
            ),
        ]

    @property
    def is_open(self):
        return self.status == self.Status.OPEN

    def __str__(self):
        return f'Смена #{self.pk} · {self.cashier} · {self.get_status_display()}'
