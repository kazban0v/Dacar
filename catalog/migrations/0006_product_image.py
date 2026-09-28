from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0005_stockmovement_writeoff_tracking'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='image',
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to='products/',
                verbose_name='Фото товара',
                help_text='WebP или JPEG, 800×800 рекомендуется',
            ),
        ),
    ]
