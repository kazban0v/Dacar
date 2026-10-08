from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, Product


class CatalogQualityFiltersTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username='catalog-admin', password='test-password', role=user_model.Role.ADMIN,
        )
        self.category = Category.objects.create(name='Тест', slug='test')
        self.without_photo = Product.objects.create(
            name='Товар без фото', sku='NO-PHOTO', barcode='200000000010', category=self.category,
            purchase_price=Decimal('100'), retail_price=Decimal('200'), stock_qty=1,
        )
        Product.objects.create(
            name='Товар с фото', sku='WITH-PHOTO', barcode='200000000011', category=self.category,
            purchase_price=Decimal('100'), retail_price=Decimal('200'), stock_qty=1,
            image='products/with-photo.webp',
        )

    def test_missing_image_filter_only_returns_products_without_images(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('product_list'), {'quality': 'missing_image'})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.without_photo.name)
        self.assertNotContains(response, 'Товар с фото')
        self.assertEqual(response.context['quality_counts']['missing_image'], 1)
