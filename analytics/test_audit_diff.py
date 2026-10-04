from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model

from analytics.audit_helpers import build_change_diff, diff_description
from analytics.models import AuditLog
from catalog.models import Category, Product
from catalog.templatetags.dacar_format import tenge, category_icon


class AuditDiffAndFormattingTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_user(username='audit-admin', role='ADMIN')
        self.cashier = User.objects.create_user(username='audit-cashier', role='CASHIER', is_active=True)
        self.category = Category.objects.create(name='Автохимия и аксессуары', slug='himia')
        self.product = Product.objects.create(
            name='Полироль кузова FastWax',
            sku='WAX-001',
            category=self.category,
            purchase_price=Decimal('1500.00'),
            retail_price=Decimal('3000.00'),
            stock_qty=Decimal('20.000'),
        )

    def test_build_change_diff_helper(self):
        old_data = {'retail_price': Decimal('3000'), 'name': 'FastWax', 'sku': 'SAME'}
        new_data = {'retail_price': Decimal('3500'), 'name': 'FastWax Pro', 'sku': 'SAME'}
        diff = build_change_diff(old_data, new_data)

        self.assertIn('changes', diff)
        fields = {c['field']: c for c in diff['changes']}
        self.assertIn('retail_price', fields)
        self.assertEqual(fields['retail_price']['old'], '3 000')
        self.assertEqual(fields['retail_price']['new'], '3 500')
        self.assertEqual(fields['name']['old'], 'FastWax')
        self.assertEqual(fields['name']['new'], 'FastWax Pro')
        self.assertNotIn('sku', fields)

        desc = diff_description(diff)
        self.assertIn('Розничная цена: «3 000» → «3 500»', desc)
        self.assertIn('Название: «FastWax» → «FastWax Pro»', desc)

    def test_product_update_records_structured_diff(self):
        self.client.force_login(self.admin)
        resp = self.client.post(f'/catalog/{self.product.pk}/edit/', {
            'name': 'Полироль кузова FastWax Super',
            'sku': 'WAX-001',
            'barcode': '487000999888',
            'category': self.category.pk,
            'purchase_price': '1600.00',
            'retail_price': '34000.00',
            'min_stock_alert': '5',
            'unit': 'шт',
        })
        self.assertIn(resp.status_code, (200, 302))

        log = AuditLog.objects.filter(action_type=AuditLog.ActionType.PRODUCT_UPDATE).first()
        self.assertIsNotNone(log)
        self.assertIn('changes', log.metadata)
        changes = {c['field']: c for c in log.metadata['changes']}
        self.assertIn('retail_price', changes)
        self.assertEqual(changes['retail_price']['old'], '3 000')
        self.assertEqual(changes['retail_price']['new'], '34 000')

    def test_user_status_and_role_change_records_structured_diff(self):
        self.client.force_login(self.admin)

        # Toggle status (block cashier)
        resp = self.client.post('/users/staff/', {
            'action': 'toggle_status',
            'user_id': self.cashier.pk,
        })
        self.assertIn(resp.status_code, (200, 302))

        log = AuditLog.objects.filter(
            action_type=AuditLog.ActionType.USER_ACTION,
            metadata__action='toggle_status',
        ).first()
        self.assertIsNotNone(log)
        self.assertIn('changes', log.metadata)
        self.assertEqual(log.metadata['changes'][0]['field'], 'is_active')
        self.assertEqual(log.metadata['changes'][0]['new'], 'Заблокирован')

        # Change role to ADMIN
        resp2 = self.client.post('/users/staff/', {
            'action': 'set_role',
            'user_id': self.cashier.pk,
            'role': 'ADMIN',
        })
        self.assertIn(resp2.status_code, (200, 302))

        role_log = AuditLog.objects.filter(
            action_type=AuditLog.ActionType.USER_ACTION,
            metadata__action='set_role',
        ).first()
        self.assertIsNotNone(role_log)
        self.assertIn('changes', role_log.metadata)
        self.assertEqual(role_log.metadata['changes'][0]['field'], 'role')
        self.assertEqual(role_log.metadata['changes'][0]['new'], 'Администратор')

    def test_stock_in_records_structured_diff(self):
        self.client.force_login(self.admin)
        resp = self.client.post('/catalog/api/stock-action/', {
            'client_sync_id': 'audit-stock-in-123456',
            'product_id': self.product.pk,
            'action': 'IN',
            'quantity': '7',
        }, content_type='application/json')
        self.assertEqual(resp.status_code, 200, resp.content)

        log = AuditLog.objects.filter(action_type=AuditLog.ActionType.STOCK_IN).first()
        self.assertIsNotNone(log)
        self.assertIn('changes', log.metadata)
        changes = {c['field']: c for c in log.metadata['changes']}
        self.assertEqual(changes['stock_qty']['old'], '20')
        self.assertEqual(changes['stock_qty']['new'], '27')
        self.assertIn('Остаток: «20» → «27»', log.description)

    def test_tenge_formatting_filter(self):
        # Whole numbers formatted with space separator as requested (34 000 ₸)
        self.assertEqual(tenge(34000), '34 000')
        self.assertEqual(tenge('34000'), '34 000')
        self.assertEqual(tenge(1000000), '1 000 000')
        self.assertEqual(tenge(0), '0')
        self.assertEqual(tenge('0'), '0')

        # Fractional numbers formatted with comma
        self.assertEqual(tenge(1250.25), '1 250,25')
        self.assertEqual(tenge(Decimal('1250.25')), '1 250,25')

    def test_category_icon_fallback(self):
        self.assertEqual(category_icon('автошампунь'), 'fa-soap')
        self.assertEqual(category_icon('тряпки'), 'fa-hand-sparkles')
        self.assertEqual(category_icon('Неизвестная категория'), 'fa-tag')
