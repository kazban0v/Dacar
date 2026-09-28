from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0003_expand_staff_roles'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[('ADMIN', 'Администратор'), ('CASHIER', 'Кассир')],
                default='CASHIER',
                max_length=20,
                verbose_name='Роль',
            ),
        ),
    ]
