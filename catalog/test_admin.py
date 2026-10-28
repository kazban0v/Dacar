from decimal import Decimal

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.test import TestCase
from django.urls import reverse

from catalog.models import Product, StockMovement
from sales.models import SaleOrder, SaleOrderItem
from users.models import User


class ProductAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='admin-ui', password='strong-test-password', role=User.Role.ADMIN
        )
        self.client.force_login(self.admin)

    def make_product(self, suffix):
        return Product.objects.create(
            name=f'Тестовый товар {suffix}',
            sku=f'ADMIN-{suffix}',
            barcode=f'990000000{suffix}',
            retail_price=Decimal('2500.00'),
            purchase_price=Decimal('1500.00'),
            stock_qty=Decimal('2.000'),
        )

    def test_unused_product_with_stock_history_can_be_deleted(self):
        product = self.make_product('001')
        StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.MovementType.IN,
            quantity=Decimal('2.000'),
            created_by=self.admin,
        )

        url = reverse('admin:catalog_product_delete', args=[product.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Да, удалить')

        response = self.client.post(url, {'post': 'yes'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(pk=product.pk).exists())
        self.assertFalse(StockMovement.objects.filter(product_id=product.pk).exists())

    def test_product_used_in_receipt_is_protected(self):
        product = self.make_product('002')
        order = SaleOrder.objects.create(
            order_number='ADMIN-ORDER-001',
            cashier=self.admin,
            total_amount=Decimal('2500.00'),
        )
        SaleOrderItem.objects.create(
            order=order,
            product=product,
            quantity=Decimal('1.000'),
            product_name_snapshot=product.name,
            sku_snapshot=product.sku,
            unit_snapshot='шт',
            purchase_price_snapshot=Decimal('1500.00'),
            unit_price=Decimal('2500.00'),
            total_amount=Decimal('2500.00'),
        )

        url = reverse('admin:catalog_product_delete', args=[product.pk])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertTrue(Product.objects.filter(pk=product.pk).exists())

    def test_safe_bulk_delete_skips_products_used_in_receipts(self):
        unused = self.make_product('003')
        used = self.make_product('004')
        order = SaleOrder.objects.create(order_number='ADMIN-ORDER-002', cashier=self.admin)
        SaleOrderItem.objects.create(
            order=order,
            product=used,
            quantity=Decimal('1.000'),
            product_name_snapshot=used.name,
            sku_snapshot=used.sku,
            unit_snapshot='шт',
            purchase_price_snapshot=Decimal('1500.00'),
            unit_price=Decimal('2500.00'),
            total_amount=Decimal('2500.00'),
        )

        url = reverse('admin:catalog_product_changelist')
        selection = {
            'action': 'delete_unused_products',
            ACTION_CHECKBOX_NAME: [unused.pk, used.pk],
            'index': '0',
        }
        confirmation = self.client.post(url, selection)
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, 'Удалить 1')
        self.assertTrue(Product.objects.filter(pk=unused.pk).exists())

        response = self.client.post(url, {**selection, 'confirm': 'yes'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(pk=unused.pk).exists())
        self.assertTrue(Product.objects.filter(pk=used.pk).exists())

    def test_product_list_uses_redesigned_readable_table(self):
        self.make_product('005')
        response = self.client.get(reverse('admin:catalog_product_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'dc-page-header')
        self.assertContains(response, 'Удалить только товары без продаж')
        self.assertNotContains(response, 'name="form-0-retail_price"')
