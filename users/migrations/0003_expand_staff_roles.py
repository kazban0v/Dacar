from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_alter_user_role'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[
                    ('ADMIN', 'Администратор'),
                    ('CASHIER', 'Кассир'),
                    ('STOCKKEEPER', 'Кладовщик'),
                    ('MANAGER', 'Менеджер'),
                ],
                default='CASHIER',
                max_length=20,
                verbose_name='Роль',
            ),
        ),
    ]
