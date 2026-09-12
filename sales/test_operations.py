from decimal import Decimal

from django.test import TestCase

from catalog.models import Product, StockMovement
from sales.models import SaleOrder
from users.models import User


class FinancialOperationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='op-admin', role='ADMIN')
        self.product = Product.objects.create(
            name='Oil', sku='OP-OIL', barcode='OP-OIL', purchase_price=40,
            retail_price=100, stock_qty=10,
        )
        self.client.force_login(self.user)
        self.payload = {
            'client_sync_id': 'operation-key-123456',
            'items': [{'product_id': self.product.pk, 'quantity': '2', 'discount': '10'}],
            'payment_method': 'CASH', 'paid_amount': '190', 'discount_amount': '5',
        }

    def test_checkout_uses_server_price_and_all_discounts(self):
        response = self.client.post('/sales/api/checkout/', self.payload, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        order = SaleOrder.objects.get()
        self.assertEqual(order.subtotal_amount, Decimal('200.00'))
        self.assertEqual(order.discount_amount, Decimal('15.00'))
        self.assertEqual(order.total_amount, Decimal('185.00'))
        self.assertEqual(order.change_amount, Decimal('5.00'))
        self.assertEqual(order.items.get().total_amount, Decimal('190.00'))

    def test_same_checkout_key_returns_same_order_without_second_sale(self):
        first = self.client.post('/sales/api/checkout/', self.payload, content_type='application/json')
        second = self.client.post('/sales/api/checkout/', self.payload, content_type='application/json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()['replayed'])
        self.assertEqual(SaleOrder.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, Decimal('8.000'))
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_reusing_key_with_changed_payload_is_conflict(self):
        self.client.post('/sales/api/checkout/', self.payload, content_type='application/json')
        changed = {**self.payload, 'paid_amount': '200'}
        response = self.client.post('/sales/api/checkout/', changed, content_type='application/json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(SaleOrder.objects.count(), 1)

    def test_refund_twice_restores_stock_once(self):
        created = self.client.post('/sales/api/checkout/', self.payload, content_type='application/json')
        order_id = created.json()['order']['id']
        first = self.client.post(f'/sales/orders/{order_id}/refund/', {'refund_reason': 'Client return'})
        second = self.client.post(f'/sales/orders/{order_id}/refund/', {'refund_reason': 'Client return'})
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, Decimal('10.000'))
        self.assertEqual(StockMovement.objects.filter(movement_type='RETURN').count(), 1)
        self.assertEqual(SaleOrder.objects.get(pk=order_id).status, 'REFUNDED')

    def test_non_cash_payment_must_be_exact(self):
        payload = {**self.payload, 'client_sync_id': 'operation-key-card-1234',
                   'payment_method': 'CARD', 'paid_amount': '200'}
        response = self.client.post('/sales/api/checkout/', payload, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SaleOrder.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, Decimal('10.000'))
