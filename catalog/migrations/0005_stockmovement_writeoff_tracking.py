# Generated manually for explicit, auditable inventory write-offs.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0004_stockmovement_client_sync_id_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='stockmovement',
            name='movement_type',
            field=models.CharField(
                choices=[
                    ('IN', 'Приход / Поступление'),
                    ('OUT', 'Списание / Потеря'),
                    ('TRANSFER_TO_SHOP', 'Отгрузка в магазин'),
                    ('ADJUSTMENT', 'Корректировка инвентаризации'),
                    ('SALE', 'Продажа'),
                    ('RETURN', 'Возврат от покупателя'),
                    ('WRITE_OFF_REVERSAL', 'Отмена списания'),
                ],
                max_length=20,
                verbose_name='Тип операции',
            ),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='writeoff_reason',
            field=models.CharField(
                blank=True,
                choices=[
                    ('ADVERTISING', 'Реклама / съёмка видео'),
                    ('DAMAGED', 'Повреждение / брак'),
                    ('EXPIRED', 'Истёк срок годности'),
                    ('INTERNAL_USE', 'Использование для работы'),
                    ('LOST', 'Потеря / недостача'),
                    ('SAMPLE', 'Образец / тестирование'),
                    ('OTHER', 'Другое'),
                ],
                max_length=32,
                verbose_name='Причина списания',
            ),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='reversed_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Списание отменено'),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='reversal_of',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reversal_entry',
                to='catalog.stockmovement',
                verbose_name='Отменённое списание',
            ),
        ),
        migrations.AddField(
            model_name='stockmovement',
            name='reversed_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='reversed_stock_writeoffs',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Кто отменил списание',
            ),
        ),
    ]
