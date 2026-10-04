from decimal import Decimal
import json
import unittest

from django.test import TestCase
from django.contrib.auth import get_user_model

from analytics.models import AuditLog
from catalog.models import Category, Product
from sales.models import SaleOrder
from users.models import CashShift


@unittest.skip('Кассовые смены отключены: исторические записи сохранены, новые смены не создаются.')
class CashShiftAndReturnTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.cashier = User.objects.create_user(username='shift-cashier', role='CASHIER', password='password123')
        self.other_cashier = User.objects.create_user(username='other-cashier', role='CASHIER', password='password123')
        self.admin = User.objects.create_user(username='shift-admin', role='ADMIN', password='password123')

        self.category = Category.objects.create(name='Автотовары', slug='autotovary')
        self.product = Product.objects.create(
            name='Омыватель стекол Winter -30',
            sku='WASH-001',
            barcode='487000111222',
            category=self.category,
            purchase_price=Decimal('1000.00'),
            retail_price=Decimal('2500.00'),
            stock_qty=Decimal('50.000'),
        )

    def test_open_current_and_close_shift_lifecycle(self):
        self.client.force_login(self.cashier)

        # 1. No shift initially
        resp = self.client.get('/api/shifts/current/')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()['shift'])

        # 2. Open shift with 10 000 KZT
        resp = self.client.post('/api/shifts/open/', json.dumps({
            'opening_cash': '10000',
            'opening_note': 'Утренняя смена',
        }), content_type='application/json')
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertTrue(data['success'])
        shift_id = data['shift']['id']
        shift = CashShift.objects.get(id=shift_id)
        self.assertEqual(shift.opening_cash, Decimal('10000.00'))
        self.assertEqual(shift.status, CashShift.Status.OPEN)

        # 3. Cannot open another shift while one is open (409 Conflict)
        resp2 = self.client.post('/api/shifts/open/', json.dumps({
            'opening_cash': '5000',
        }), content_type='application/json')
        self.assertEqual(resp2.status_code, 409)

        # 4. Check current shift live totals
        resp_curr = self.client.get('/api/shifts/current/')
        self.assertEqual(resp_curr.status_code, 200)
        self.assertIsNotNone(resp_curr.json()['shift'])
        self.assertEqual(resp_curr.json()['expected_cash'], '10000.00')

        # 5. Make a sale in this shift (1 item = 2500 cash)
        checkout_resp = self.client.post('/sales/api/checkout/', {
            'client_sync_id': 'shift-sale-123456789',
            'items': [{'product_id': self.product.pk, 'quantity': '1', 'discount': '0'}],
            'payment_method': 'CASH',
            'paid_amount': '3000',
            'discount_amount': '0',
        }, content_type='application/json')
        self.assertEqual(checkout_resp.status_code, 201)
        order = SaleOrder.objects.get(order_number=checkout_resp.json()['order']['order_number'])
        self.assertEqual(order.shift_id, shift_id)

        # Check totals now: expected_cash should be 10000 + 2500 = 12500
        resp_curr2 = self.client.get('/api/shifts/current/')
        totals = resp_curr2.json()['totals']
        self.assertEqual(Decimal(totals['total_revenue']), Decimal('2500.00'))
        self.assertEqual(Decimal(totals['cash_revenue']), Decimal('2500.00'))
        self.assertEqual(Decimal(resp_curr2.json()['expected_cash']), Decimal('12500.00'))

        # 6. Close shift with counted cash 12500 (even)
        close_resp = self.client.post(f'/api/shifts/{shift_id}/close/', json.dumps({
            'counted_cash': '12500',
            'closing_note': 'Все сошлось копейка в копейку',
        }), content_type='application/json')
        self.assertEqual(close_resp.status_code, 200)
        close_data = close_resp.json()
        self.assertTrue(close_data['success'])
        self.assertEqual(close_data['difference'], '0.00')

        shift.refresh_from_db()
        self.assertEqual(shift.status, CashShift.Status.CLOSED)
        self.assertEqual(shift.counted_cash, Decimal('12500.00'))
        self.assertEqual(Decimal(close_data['expected_cash']), Decimal('12500.00'))
        self.assertEqual(Decimal(close_data['difference']), Decimal('0.00'))
        self.assertIsNotNone(shift.closed_at)

        # Check audit log for shift open and close
        open_log = AuditLog.objects.filter(action_type=AuditLog.ActionType.USER_ACTION, metadata__action='shift_open').first()
        self.assertIsNotNone(open_log)
        self.assertEqual(open_log.metadata['opening_cash'], '10000')

        close_log = AuditLog.objects.filter(action_type=AuditLog.ActionType.USER_ACTION, metadata__action='shift_close').first()
        self.assertIsNotNone(close_log)
        self.assertEqual(Decimal(close_log.metadata['counted_cash']), Decimal('12500.00'))
        self.assertEqual(Decimal(close_log.metadata['difference']), Decimal('0.00'))

    def test_refund_in_subsequent_shift_deducts_cash_correctly(self):
        # Admin creates order yesterday / in previous shift
        self.client.force_login(self.admin)
        old_shift = CashShift.objects.create(
            cashier=self.admin,
            opening_cash=Decimal('5000.00'),
            status=CashShift.Status.CLOSED,
        )
        prev_order = SaleOrder.objects.create(
            order_number='DACAR-OLD-999',
            cashier=self.admin,
            shift=old_shift,
            status=SaleOrder.Status.COMPLETED,
            payment_method='CASH',
            subtotal_amount=Decimal('5000.00'),
            total_amount=Decimal('5000.00'),
            paid_amount=Decimal('5000.00'),
            change_amount=Decimal('0.00'),
        )

        # Now cashier opens new shift with 20 000 cash
        self.client.force_login(self.cashier)
        self.client.post('/api/shifts/open/', json.dumps({
            'opening_cash': '20000',
        }), content_type='application/json')
        current_shift = CashShift.objects.get(cashier=self.cashier, status=CashShift.Status.OPEN)

        # Cashier looks up the previous order via lookup API
        lookup_resp = self.client.get(f'/sales/api/orders/lookup/?q={prev_order.order_number}')
        self.assertEqual(lookup_resp.status_code, 200)
        self.assertTrue(lookup_resp.json()['success'])
        self.assertEqual(lookup_resp.json()['order']['order_number'], 'DACAR-OLD-999')

        # Cashier processes refund for this old order
        refund_resp = self.client.post(
            f'/sales/orders/{prev_order.pk}/refund/',
            {'refund_reason': 'Брак упаковки'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(refund_resp.status_code, 200)
        self.assertTrue(refund_resp.json()['success'])

        prev_order.refresh_from_db()
        self.assertEqual(prev_order.status, SaleOrder.Status.REFUNDED)
        self.assertEqual(prev_order.refund_shift_id, current_shift.id)

        # Live totals for current shift should show refund and expected cash = 20000 - 5000 = 15000
        curr_resp = self.client.get('/api/shifts/current/')
        totals = curr_resp.json()['totals']
        self.assertEqual(int(totals['refund_count']), 1)
        self.assertEqual(Decimal(totals['refund_amount']), Decimal('5000.00'))
        self.assertEqual(Decimal(totals['cash_refunds']), Decimal('5000.00'))
        self.assertEqual(Decimal(curr_resp.json()['expected_cash']), Decimal('15000.00'))

        # Cashier closes shift with 14 800 (short 200)
        close_resp = self.client.post(f'/api/shifts/{current_shift.id}/close/', json.dumps({
            'counted_cash': '14800',
            'closing_note': 'Недостача 200 ₸',
        }), content_type='application/json')
        self.assertEqual(close_resp.status_code, 200)
        self.assertEqual(Decimal(close_resp.json()['difference']), Decimal('-200.00'))
