"""Role boundaries, including forged requests bypassing the visible buttons."""
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings

from analytics.models import AuditLog, NotificationState
from catalog.models import Product, StockMovement
from sales.models import SaleOrder
from users.models import User


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RolePermissionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username='business-admin', role='ADMIN')
        cls.cashier = User.objects.create_user(username='cashier', role='CASHIER')
        cls.other = User.objects.create_user(username='other-cashier', role='CASHIER')
        cls.owner = User.objects.create_superuser(username='technical-owner', password='test-only')
        cls.product = Product.objects.create(name='Test product', sku='TEST', barcode='TEST',
            purchase_price=40, retail_price=100, stock_qty=20)
        cls.order = SaleOrder.objects.create(order_number='OWN-RECEIPT', cashier=cls.cashier,
            status='COMPLETED', payment_method='CASH', subtotal_amount=100,
            total_amount=100, paid_amount=100)
        cls.other_order = SaleOrder.objects.create(order_number='OTHER-RECEIPT', cashier=cls.other,
            status='COMPLETED', payment_method='CASH', subtotal_amount=200,
            total_amount=200, paid_amount=200)

    def test_cashier_cannot_change_stock_even_with_replay_key(self):
        self.client.force_login(self.cashier)
        for prefix in ('', '/m'):
            for action in ('IN', 'OUT', 'TRANSFER_TO_SHOP'):
                response = self.client.post(f'{prefix}/catalog/api/stock-action/', {
                    'product_id': self.product.pk, 'action': action, 'quantity': 1,
                    'client_sync_id': 'forged-id'}, content_type='application/json')
                self.assertEqual(response.status_code, 403)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 20)
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_cashier_cannot_create_edit_products_or_categories(self):
        self.client.force_login(self.cashier)
        for prefix in ('', '/m'):
            for path in ('add/', f'{self.product.pk}/edit/', 'stock/movements/'):
                response = self.client.post(f'{prefix}/catalog/{path}', {
                    'name': 'Forged', 'retail_price': 1, 'stock_qty': 999,
                    'product_id': self.product.pk, 'quantity': 99, 'movement_type': 'IN'})
                self.assertEqual(response.status_code, 302)
            self.assertEqual(self.client.post(f'{prefix}/catalog/api/categories/create/',
                {'name': 'Forged'}).status_code, 403)
        self.product.refresh_from_db()
        self.assertEqual(self.product.name, 'Test product')
        self.assertEqual(self.product.retail_price, 100)
        self.assertEqual(Product.objects.count(), 1)
        self.assertFalse(StockMovement.objects.exists())

    def test_admin_stock_api_and_category_creation_still_work(self):
        self.client.force_login(self.admin)
        for prefix in ('', '/m'):
            self.assertEqual(self.client.post(f'{prefix}/catalog/api/stock-action/', {
                'product_id': self.product.pk, 'action': 'IN', 'quantity': 1,
            }, content_type='application/json').status_code, 200)
            self.assertEqual(self.client.post(f'{prefix}/catalog/api/categories/create/',
                {'name': 'New category' + prefix}).status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 22)
        self.assertEqual(StockMovement.objects.count(), 2)

    def test_cashier_lists_only_own_orders_despite_forged_filters(self):
        self.client.force_login(self.cashier)
        for prefix in ('', '/m'):
            response = self.client.get(f'{prefix}/sales/orders/', {
                'cashier': self.other.pk, 'cashier_id': self.other.pk})
            self.assertContains(response, self.order.order_number)
            self.assertNotContains(response, self.other_order.order_number)

    def test_admin_can_view_all_orders_and_print_other_receipts(self):
        self.client.force_login(self.admin)
        for prefix in ('', '/m'):
            response = self.client.get(f'{prefix}/sales/orders/')
            self.assertContains(response, self.order.order_number)
            self.assertContains(response, self.other_order.order_number)
            self.assertEqual(self.client.get(
                f'{prefix}/sales/orders/{self.other_order.pk}/print/').status_code, 200)

    def test_cashier_cannot_refund_or_delete_any_order(self):
        self.client.force_login(self.cashier)
        for prefix in ('', '/m'):
            for order in (self.order, self.other_order):
                for action in ('refund', 'delete'):
                    self.assertEqual(self.client.post(f'{prefix}/sales/orders/{order.pk}/{action}/',
                        {'refund_reason': 'Forged refund'}).status_code, 302)
                order.refresh_from_db()
                self.assertEqual(order.status, 'COMPLETED')
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_unsupported_manager_role_does_not_grant_refunds(self):
        self.cashier.role = 'MANAGER'
        self.cashier.save()
        self.client.force_login(self.cashier)
        for prefix in ('', '/m'):
            self.client.post(f'{prefix}/sales/orders/{self.order.pk}/refund/',
                {'refund_reason': 'Forged refund'})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'COMPLETED')

    def test_cashier_cannot_manage_staff(self):
        self.client.force_login(self.cashier)
        for prefix in ('', '/m'):
            for action in ('create', 'set_role', 'toggle_status', 'delete'):
                self.assertEqual(self.client.post(f'{prefix}/users/staff/', {
                    'action': action, 'user_id': self.other.pk, 'username': 'forged',
                    'password': 'test-only', 'role': 'ADMIN'}).status_code, 302)
        self.other.refresh_from_db()
        self.assertTrue(self.other.is_active)
        self.assertEqual(self.other.role, 'CASHIER')
        self.assertFalse(User.objects.filter(username='forged').exists())

    def test_staff_create_rejects_invalid_role_and_ignores_technical_flags(self):
        self.client.force_login(self.admin)
        for prefix in ('', '/m'):
            self.client.post(f'{prefix}/users/staff/', {'action': 'create',
                'username': 'forged', 'password': 'test-only', 'role': 'MANAGER'})
            self.assertFalse(User.objects.filter(username='forged').exists())
            name = 'valid' + ('mobile' if prefix else 'desktop')
            self.client.post(f'{prefix}/users/staff/', {'action': 'create',
                'username': name, 'password': 'test-only', 'role': 'ADMIN',
                'is_staff': '1', 'is_superuser': '1'})
            created = User.objects.get(username=name)
            self.assertEqual(created.role, 'ADMIN')
            self.assertFalse(created.is_staff)
            self.assertFalse(created.is_superuser)

    def test_business_admin_cannot_disable_demote_or_delete_technical_accounts(self):
        technical = User.objects.create_user(username='technical-staff', is_staff=True, role='ADMIN')
        self.client.force_login(self.admin)
        for prefix in ('', '/m'):
            for target in (self.owner, technical, self.admin):
                for action in ('toggle_status', 'set_role', 'delete'):
                    self.client.post(f'{prefix}/users/staff/', {
                        'action': action, 'user_id': target.pk, 'role': 'CASHIER'})
                original_role = target.role
                target.refresh_from_db()
                self.assertTrue(target.is_active)
                self.assertEqual(target.role, original_role)

    def test_business_admin_can_manage_ordinary_staff(self):
        self.client.force_login(self.admin)
        self.client.post('/users/staff/', {'action': 'set_role', 'user_id': self.other.pk, 'role': 'ADMIN'})
        self.other.refresh_from_db()
        self.assertEqual(self.other.role, 'ADMIN')
        self.client.post('/m/users/staff/', {'action': 'toggle_status', 'user_id': self.other.pk})
        self.other.refresh_from_db()
        self.assertFalse(self.other.is_active)

    @override_settings(ALLOW_REGISTRATION=True)
    def test_public_registration_cannot_self_assign_admin(self):
        for prefix in ('', '/m'):
            self.client.logout()
            name = 'public' + ('mobile' if prefix else 'desktop')
            self.client.post(f'{prefix}/users/register/', {'username': name, 'password': 'test-only',
                'role': 'ADMIN', 'is_staff': '1', 'is_superuser': '1'})
            user = User.objects.get(username=name)
            self.assertEqual(user.role, 'CASHIER')
            self.assertFalse(user.is_staff)
            self.assertFalse(user.is_superuser)

    def test_cashier_cannot_access_store_analytics_or_ai(self):
        self.client.force_login(self.cashier)
        for prefix in ('', '/m'):
            for path in ('api/analytics/data/', 'api/analytics/live-kpi/', 'analytics/export.csv'):
                self.assertEqual(self.client.get(f'{prefix}/{path}').status_code, 403)
            self.assertEqual(self.client.get(f'{prefix}/audit-log/').status_code, 302)
            with patch('analytics.ai.generate_insight') as ai:
                self.assertEqual(self.client.post(f'{prefix}/api/analytics/ai-insight/',
                    {}, content_type='application/json').status_code, 403)
                ai.assert_not_called()

    def test_cashier_cannot_read_purchase_cost_or_margin_from_catalog_api(self):
        for user in (self.cashier, self.admin):
            self.client.force_login(user)
            response = self.client.get('/catalog/api/search/', {'barcode': self.product.barcode})
            self.assertEqual(response.status_code, 200)
            payload = response.json()['product']
            if user.is_admin_user:
                self.assertIn('purchase_price', payload)
            else:
                self.assertNotIn('purchase_price', payload)

    def test_cashier_cannot_read_internal_costs_from_checkout_response(self):
        self.client.force_login(self.cashier)
        response = self.client.post('/sales/api/checkout/', {
            'client_sync_id': 'cashier-response-cost-key',
            'items': [{'product_id': self.product.pk, 'quantity': '1', 'discount': '0'}],
            'payment_method': 'CASH', 'paid_amount': '100',
        }, content_type='application/json')
        self.assertEqual(response.status_code, 201)
        payload = response.json()['order']
        self.assertNotIn('gross_profit', payload)
        self.assertNotIn('purchase_price_snapshot', payload['items'][0])

    def test_cashier_catalog_page_hides_inventory_cost_columns(self):
        self.client.force_login(self.cashier)
        response = self.client.get('/catalog/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<th>Закупка</th>')
        self.assertNotContains(response, 'class="stock-cost"')
        self.assertNotContains(response, '<th>Маржа</th>')
        self.assertNotContains(response, 'class="stock-margin"')
        self.client.force_login(self.admin)
        response = self.client.get('/catalog/')
        self.assertContains(response, 'stock-cost')
        self.assertContains(response, 'stock-margin')

    def test_notifications_are_scoped_to_current_user(self):
        AuditLog.objects.create(user=self.cashier, action_type='SALE', description='Own event')
        AuditLog.objects.create(user=self.other, action_type='SALE', description='Private other event')
        other_state = NotificationState.objects.create(user=self.other)
        self.client.force_login(self.cashier)
        response = self.client.get('/api/notifications/')
        self.assertContains(response, 'Own event')
        self.assertNotContains(response, 'Private other event')
        self.client.post('/api/notifications/action/', {'action': 'clear_all', 'user_id': self.other.pk},
            content_type='application/json')
        other_state.refresh_from_db()
        self.assertIsNone(other_state.cleared_before)

    def test_staff_cashier_cannot_bypass_role_using_django_admin_permissions(self):
        self.cashier.is_staff = True
        self.cashier.save()
        self.cashier.user_permissions.set(Permission.objects.all())
        self.client.force_login(self.cashier)
        for path in ('', 'catalog/product/', f'catalog/product/{self.product.pk}/change/',
                     'sales/saleorder/', 'users/user/', 'monitor/data/'):
            self.assertEqual(self.client.get('/admin/' + path).status_code, 302)
        self.assertEqual(self.client.post(f'/admin/catalog/product/{self.product.pk}/delete/',
            {'post': 'yes'}).status_code, 302)
        self.assertTrue(Product.objects.filter(pk=self.product.pk).exists())

    def test_business_admin_needs_explicit_staff_access_and_model_permissions(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.admin.is_staff = True
        self.admin.save()
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(self.client.get('/admin/catalog/product/').status_code, 403)
        self.admin.user_permissions.add(Permission.objects.get(codename='view_product'))
        self.assertEqual(self.client.get('/admin/catalog/product/').status_code, 200)

    def test_django_user_admin_prevents_privilege_escalation_and_owner_password_change(self):
        self.admin.is_staff = True
        self.admin.save()
        self.admin.user_permissions.set(Permission.objects.all())
        self.client.force_login(self.admin)
        for path in ('change/', 'password/', 'delete/'):
            self.assertEqual(self.client.post(f'/admin/users/user/{self.owner.pk}/{path}',
                {'password1': 'forged-password', 'password2': 'forged-password', 'post': 'yes'}).status_code, 403)
        response = self.client.post(f'/admin/users/user/{self.other.pk}/change/', {
            'username': self.other.username, 'role': 'ADMIN', 'is_active': 'on',
            'date_joined_0': '2026-09-12', 'date_joined_1': '12:00:00',
            'is_staff': 'on', 'is_superuser': 'on',
            'user_permissions': list(Permission.objects.values_list('pk', flat=True)), '_save': 'Save'})
        self.assertEqual(response.status_code, 302,
            response.context['adminform'].form.errors if response.status_code == 200 else '')
        self.other.refresh_from_db()
        self.assertFalse(self.other.is_staff)
        self.assertFalse(self.other.is_superuser)
        self.assertFalse(self.other.user_permissions.exists())

    def test_django_user_admin_add_uses_password_form_and_ignores_forged_flags(self):
        self.admin.is_staff = True
        self.admin.save()
        self.admin.user_permissions.set(Permission.objects.all())
        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/admin/users/user/add/'), 'name="password1"')
        response = self.client.post('/admin/users/user/add/', {
            'username': 'admin-created', 'password1': 'Strong-test-pass-579!',
            'password2': 'Strong-test-pass-579!', 'role': 'ADMIN', 'is_staff': 'on', 'is_superuser': 'on'})
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='admin-created')
        self.assertTrue(user.check_password('Strong-test-pass-579!'))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_bulk_delete_cannot_remove_protected_accounts(self):
        self.admin.is_staff = True
        self.admin.save()
        self.admin.user_permissions.set(Permission.objects.all())
        self.client.force_login(self.admin)
        response = self.client.post('/admin/users/user/', {'action': 'delete_selected',
            '_selected_action': [self.owner.pk, self.admin.pk], 'post': 'yes'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_inactive_session_cannot_mutate_stock(self):
        self.client.force_login(self.admin)
        self.admin.is_active = False
        self.admin.save()
        response = self.client.post('/catalog/api/stock-action/', {
            'product_id': self.product.pk, 'quantity': 1}, content_type='application/json')
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 20)

    def test_mobile_warehouse_hides_admin_actions_from_cashier(self):
        self.client.force_login(self.cashier)
        response = self.client.get('/m/catalog/')
        self.assertContains(response, 'Test product')
        self.assertNotContains(response, 'aria-label="Добавить товар"')
        self.assertNotContains(response, 'class="swipe-in"')
        self.assertNotContains(response, 'role="button" tabindex="0" aria-label="Действия с товаром')
        self.assertContains(response, 'const canManageStock = false;')
        self.client.force_login(self.admin)
        response = self.client.get('/m/catalog/')
        self.assertContains(response, 'aria-label="Добавить товар"')
        self.assertContains(response, 'class="swipe-in"')

    def test_staff_cards_mark_technical_users_as_protected(self):
        self.client.force_login(self.admin)
        for prefix in ('', '/m'):
            response = self.client.get(f'{prefix}/users/staff/')
            cards = {card['user'].pk: card for card in response.context['staff_cards']}
            self.assertFalse(cards[self.owner.pk]['can_manage'])
            self.assertFalse(cards[self.admin.pk]['can_manage'])
            self.assertTrue(cards[self.other.pk]['can_manage'])

    def test_superuser_keeps_user_creation_and_staff_management(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get('/admin/users/user/add/'), 'name="is_superuser"')
        self.client.post('/m/users/staff/', {'action': 'set_role', 'user_id': self.other.pk, 'role': 'ADMIN'})
        self.other.refresh_from_db()
        self.assertEqual(self.other.role, 'ADMIN')
