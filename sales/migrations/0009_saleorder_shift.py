from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0005_cashshift'),
        ('sales', '0008_backfill_sale_snapshots'),
    ]

    operations = [
        migrations.AddField(
            model_name='saleorder',
            name='shift',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='orders', to='users.cashshift', verbose_name='Кассовая смена'),
        ),
    ]
