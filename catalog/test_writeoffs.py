from decimal import Decimal

from django.test import TestCase, override_settings

from analytics.models import AuditLog
from catalog.models import Product, StockMovement
from users.models import User


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class WriteOffTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username='writeoff-admin', role='ADMIN')
        cls.cashier = User.objects.create_user(username='writeoff-cashier', role='CASHIER')
        cls.product = Product.objects.create(
            name='Video shampoo',
            sku='VIDEO-01',
            barcode='VIDEO-01',
            purchase_price='1200.00',
            retail_price='2500.00',
            stock_qty='10.000',
        )

    def write_off(self, **overrides):
        payload = {
            'product_id': self.product.pk,
            'action': 'OUT',
            'quantity': '2',
            'writeoff_reason': 'ADVERTISING',
            'comment': 'Съёмка ролика',
            'client_sync_id': 'writeoff-test-key-0001',
        }
        payload.update(overrides)
        return self.client.post('/catalog/api/stock-action/', payload,
                                content_type='application/json')

    def test_writeoff_requires_reason_and_does_not_change_stock_on_error(self):
        self.client.force_login(self.admin)
        response = self.write_off(writeoff_reason='')
        self.assertEqual(response.status_code, 400)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, Decimal('10.000'))
        self.assertFalse(StockMovement.objects.exists())

    def test_writeoff_reduces_stock_and_saves_reason_and_audit(self):
        self.client.force_login(self.admin)
        response = self.write_off()
        self.assertEqual(response.status_code, 200, response.content)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, Decimal('8.000'))
        movement = StockMovement.objects.get()
        self.assertEqual(movement.movement_type, StockMovement.MovementType.OUT)
        self.assertEqual(movement.writeoff_reason, StockMovement.WriteOffReason.ADVERTISING)
        self.assertEqual(movement.comment, 'Съёмка ролика')
        self.assertIn('Реклама / съёмка видео', AuditLog.objects.get().description)

    def test_writeoff_cannot_exceed_available_stock(self):
        self.client.force_login(self.admin)
        response = self.write_off(quantity='11')
        self.assertEqual(response.status_code, 409)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, Decimal('10.000'))
        self.assertFalse(StockMovement.objects.exists())

    def test_admin_can_reverse_writeoff_exactly_once(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.write_off().status_code, 200)
        movement = StockMovement.objects.get(movement_type='OUT')

        first = self.client.post(f'/catalog/stock/movements/{movement.pk}/reverse/')
        second = self.client.post(f'/catalog/stock/movements/{movement.pk}/reverse/')
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)

        self.product.refresh_from_db()
        movement.refresh_from_db()
        self.assertEqual(self.product.stock_qty, Decimal('10.000'))
        self.assertIsNotNone(movement.reversed_at)
        self.assertEqual(movement.reversed_by, self.admin)
        reversals = StockMovement.objects.filter(movement_type='WRITE_OFF_REVERSAL')
        self.assertEqual(reversals.count(), 1)
        self.assertEqual(reversals.get().reversal_of, movement)
        self.assertEqual(AuditLog.objects.filter(action_type='STOCK_ADJUST').count(), 1)

    def test_cashier_cannot_write_off_or_reverse(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.write_off().status_code, 403)
        movement = StockMovement.objects.create(
            product=self.product,
            movement_type='OUT',
            quantity=1,
            writeoff_reason='DAMAGED',
            created_by=self.admin,
        )
        response = self.client.post(f'/catalog/stock/movements/{movement.pk}/reverse/')
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        movement.refresh_from_db()
        self.assertEqual(self.product.stock_qty, Decimal('10.000'))
        self.assertIsNone(movement.reversed_at)

    def test_desktop_form_shows_writeoff_reasons_and_reversal_status(self):
        self.client.force_login(self.admin)
        response = self.client.get('/catalog/stock/movements/?product=%s&action=OUT' % self.product.pk)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Реклама / съёмка видео')
        self.assertContains(response, 'Причина списания')
