from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('analytics', '0003_translate_stock_audit_descriptions')]

    operations = [
        migrations.AddField(
            model_name='auditlog',
            name='metadata',
            field=models.JSONField(blank=True, default=dict, verbose_name='Структурированные данные'),
        ),
    ]
