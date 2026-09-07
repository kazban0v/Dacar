import re

from django.db import migrations


MOVEMENT_LABELS = {
    'IN': 'Приход / поступление',
    'OUT': 'Списание / потеря',
    'TRANSFER_TO_SHOP': 'Отгрузка в магазин',
    'ADJUSTMENT': 'Корректировка остатков',
    'SALE': 'Продажа',
    'RETURN': 'Возврат от покупателя',
}


def translate_stock_descriptions(apps, schema_editor):
    AuditLog = apps.get_model('analytics', 'AuditLog')
    pattern = re.compile(
        r"^Складская операция \[(?P<code>[A-Z_]+)\] для '(?P<product>.*)': "
        r"(?P<quantity>.+?)\. Комментарий: (?P<comment>.*)$"
    )

    for log in AuditLog.objects.filter(description__startswith='Складская операция [').iterator():
        match = pattern.match(log.description)
        if not match:
            continue
        code = match.group('code')
        label = MOVEMENT_LABELS.get(code, 'Складская операция')
        description = f"{label}: «{match.group('product')}» — {match.group('quantity')}"
        comment = match.group('comment').strip()
        if comment:
            description += f'. Комментарий: {comment}'
        log.description = description
        log.save(update_fields=['description'])


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0002_notificationstate'),
    ]

    operations = [
        migrations.RunPython(translate_stock_descriptions, migrations.RunPython.noop),
    ]
