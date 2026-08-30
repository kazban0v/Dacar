from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from catalog.models import Category, Brand, Product, StockMovement
from sales.models import SaleOrder, SaleOrderItem
from decimal import Decimal
from django.conf import settings

User = get_user_model()

class FinancialSecurityTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='cashier_test',
            password='password123',
            role=User.Role.CASHIER
        )
        self.client.login(username='cashier_test', password='password123')

        self.category = Category.objects.create(name='Автошампуни', slug='shampoos')
        self.product = Product.objects.create(
            name='Detail Nano Shampoo (1 л)',
            sku='DAC-101',
            barcode='4607001234501',
            category=self.category,
            purchase_price=Decimal('2800.00'),
            retail_price=Decimal('4500.00'),
            stock_qty=Decimal('10.000')
        )

    def test_server_side_price_enforcement(self):
        """Verify that client-side submitted prices are IGNORED and DB price is used."""
        payload = {
            'items': [
                {
                    'product_id': self.product.id,
                    'quantity': 2,
                    'unit_price': '1.00', # Tampered cheap price from client!
                    'discount': 0
                }
            ],
            'payment_method': 'CASH',
            'paid_amount': '10000.00',
            'discount_amount': 0
        }

        response = self.client.post(
            '/sales/api/checkout/',
            data=payload,
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 201)

        # Check DB Order - Expected total is 2 * 4500.00 = 9000.00, NOT 2 * 1.00 = 2.00
        order = SaleOrder.objects.get(order_number=response.json()['order']['order_number'])
        self.assertEqual(order.subtotal_amount, Decimal('9000.00'))
        self.assertEqual(order.total_amount, Decimal('9000.00'))
        self.assertEqual(order.change_amount, Decimal('1000.00'))

        # Check product stock deduction (10 - 2 = 8)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, Decimal('8.000'))

    def test_insufficient_stock_prevention(self):
        """Verify checkout fails when requesting more stock than available."""
        payload = {
            'items': [
                {
                    'product_id': self.product.id,
                    'quantity': 50, # Stock is only 10
                    'discount': 0
                }
            ],
            'payment_method': 'CASH',
            'paid_amount': '500000.00',
        }

        response = self.client.post(
            '/sales/api/checkout/',
            data=payload,
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('Недостаточно товара', str(response.json()))

        # Stock should remain unchanged
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, Decimal('10.000'))

    def test_registration_middleware_restriction(self):
        """Verify middleware blocks registration when ALLOW_REGISTRATION is False."""
        with self.settings(ALLOW_REGISTRATION=False):
            response = self.client.get('/users/register/')
            self.assertEqual(response.status_code, 302) # Redirects to login
            
            api_response = self.client.post('/api/users/register/')
            self.assertEqual(api_response.status_code, 403)
