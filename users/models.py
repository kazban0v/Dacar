from django.db import models
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
