# Generated manually for Django 6.0.
from decimal import Decimal

from django.db import migrations, models
from django.db.models import Q
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0004_remove_extra_staff_roles'),
    ]

    operations = [
        migrations.CreateModel(
            name='CashShift',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('OPEN', 'Открыта'), ('CLOSED', 'Закрыта')], db_index=True, default='OPEN', max_length=10)),
                ('opened_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Открыта')),
                ('closed_at', models.DateTimeField(blank=True, null=True, verbose_name='Закрыта')),
                ('opening_cash', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12, verbose_name='Размен при открытии')),
                ('counted_cash', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True, verbose_name='Наличные при закрытии')),
                ('opening_note', models.CharField(blank=True, max_length=255, verbose_name='Комментарий при открытии')),
                ('closing_note', models.CharField(blank=True, max_length=255, verbose_name='Комментарий при закрытии')),
                ('cashier', models.ForeignKey(on_delete=models.deletion.PROTECT, related_name='cash_shifts', to='users.user', verbose_name='Кассир')),
            ],
            options={'verbose_name': 'Кассовая смена', 'verbose_name_plural': 'Кассовые смены', 'ordering': ['-opened_at']},
        ),
        migrations.AddConstraint(
            model_name='cashshift',
            constraint=models.UniqueConstraint(condition=Q(('status', 'OPEN')), fields=('cashier',), name='one_open_cash_shift_per_cashier'),
        ),
    ]
