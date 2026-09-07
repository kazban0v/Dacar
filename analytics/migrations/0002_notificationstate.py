from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('read_before', models.DateTimeField(blank=True, null=True, verbose_name='Прочитано до')),
                ('cleared_before', models.DateTimeField(blank=True, null=True, verbose_name='Удалено до')),
                ('dismissed_keys', models.JSONField(blank=True, default=list, verbose_name='Скрытые уведомления')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Обновлено')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='notification_state', to=settings.AUTH_USER_MODEL, verbose_name='Пользователь')),
            ],
            options={
                'verbose_name': 'Состояние уведомлений',
                'verbose_name_plural': 'Состояния уведомлений',
            },
        ),
    ]
