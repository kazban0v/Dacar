from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product
from sales.gift_cards import code_digest
from sales.models import GiftCard, GiftCardEvent, SaleOrder, SalePayment
from users.models import User


class GiftCardFlowTests(TestCase):
    code = 'DKR-12345-ABCDE'

    def setUp(self):
        self.user = User.objects.create_user(username='gift-cashier', role=User.Role.CASHIER)
        self.client.force_login(self.user)
        self.product = Product.objects.create(
            name='Шампунь', sku='GIFT-SHAMPOO', barcode='GIFT-SHAMPOO',
            purchase_price=Decimal('3000'), retail_price=Decimal('7000'), stock_qty=10,
        )
        self.card = GiftCard.objects.create(
            code_hash=code_digest(self.code), code_last4=self.code[-4:],
            nominal=Decimal('10000.00'),
        )

    def activate(self):
        return self.client.post('/sales/gift-cards/', {
            'code': self.code, 'payment_method': 'CARD', 'payment_received': 'yes',
        })

    def checkout(self, key='gift-checkout-123456', quantity='1', **extra):
        payload = {
            'client_sync_id': key,
            'items': [{'product_id': self.product.pk, 'quantity': quantity}],
            'payment_method': 'GIFT', 'paid_amount': str(Decimal('7000') * Decimal(quantity)),
            'gift_code': self.code,
            **extra,
        }
        return self.client.post('/sales/api/checkout/', payload, content_type='application/json')

    def test_activation_starts_45_days_once_and_lookup_does_not_debit(self):
        before = timezone.now()
        self.assertEqual(self.card.state, 'UNISSUED')
        response = self.client.post('/sales/api/gift-cards/lookup/', {'code': self.code}, content_type='application/json')
        self.assertEqual(response.json()['state'], 'UNISSUED')
        self.assertEqual(self.card.balance, Decimal('0.00'))
        self.assertEqual(self.activate().status_code, 302)
        self.card.refresh_from_db()
        self.assertEqual(self.card.balance, self.card.nominal)
        self.assertGreaterEqual(self.card.expires_at, before + timedelta(days=45))
        self.assertEqual(self.card.state, 'ACTIVE')
        self.activate()
        self.assertEqual(GiftCardEvent.objects.filter(kind='ACTIVATE').count(), 1)
        response = self.client.post('/sales/api/gift-cards/lookup/', {'code': self.code}, content_type='application/json')
        self.assertEqual(response.json()['balance'], '10000.00')
        self.card.refresh_from_db()
        self.assertEqual(self.card.balance, self.card.nominal)

    def test_gift_only_sale_replay_and_refund_are_idempotent(self):
        self.activate()
        first = self.checkout()
        self.assertEqual(first.status_code, 201, first.content)
        second = self.checkout()
        self.assertEqual(second.status_code, 200, second.content)
        self.assertTrue(second.json()['replayed'])
        self.card.refresh_from_db()
        self.assertEqual(self.card.balance, Decimal('3000.00'))
        order = SaleOrder.objects.get()
        self.assertEqual(order.payments.get().method, 'GIFT')
        self.assertEqual(GiftCardEvent.objects.filter(kind='REDEEM').count(), 1)
        admin = User.objects.create_user(username='gift-admin', role=User.Role.ADMIN)
        self.client.force_login(admin)
        refund_url = f'/sales/orders/{order.pk}/refund/'
        self.assertEqual(self.client.post(refund_url, {'refund_reason': 'Покупатель вернул товар'}).status_code, 302)
        self.assertEqual(self.client.post(refund_url, {'refund_reason': 'Покупатель вернул товар'}).status_code, 302)
        self.card.refresh_from_db()
        self.assertEqual(self.card.balance, Decimal('10000.00'))
        self.assertEqual(GiftCardEvent.objects.filter(kind='REFUND').count(), 1)

    def test_mixed_payment_uses_card_and_cash_exactly(self):
        self.activate()
        response = self.checkout(
            key='gift-mixed-123456', quantity='2', payment_method='MIXED',
            paid_amount='14000.00', payments=[
                {'method': 'GIFT', 'amount': '10000.00'},
                {'method': 'CASH', 'amount': '4000.00'},
            ],
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(set(SalePayment.objects.values_list('method', flat=True)), {'GIFT', 'CASH'})
        self.card.refresh_from_db()
        self.assertEqual(self.card.balance, Decimal('0.00'))
        self.assertEqual(self.card.state, 'SPENT')
        from analytics.models import AuditLog
        sale_log = AuditLog.objects.get(action_type=AuditLog.ActionType.SALE)
        self.assertEqual(
            {part['method']: part['amount'] for part in sale_log.metadata['payments']},
            {'GIFT': '10000.00', 'CASH': '4000.00'},
        )
        self.assertEqual(sale_log.metadata['gift_card']['last4'], 'BCDE')

    def test_remaining_balance_can_be_used_on_another_purchase(self):
        self.activate()
        self.assertEqual(self.checkout().status_code, 201)
        self.product.retail_price = Decimal('3000.00')
        self.product.save(update_fields=['retail_price'])
        second = self.checkout(key='gift-second-sale-123456', paid_amount='3000.00')
        self.assertEqual(second.status_code, 201, second.content)
        self.card.refresh_from_db()
        self.assertEqual(self.card.balance, Decimal('0.00'))
        self.assertEqual(GiftCardEvent.objects.filter(kind='REDEEM').count(), 2)

    def test_mixed_payment_can_combine_card_and_kaspi_qr(self):
        self.activate()
        response = self.checkout(
            key='gift-three-methods-123456', quantity='2', payment_method='MIXED',
            paid_amount='14000.00', payments=[
                {'method': 'GIFT', 'amount': '10000.00'},
                {'method': 'CARD', 'amount': '2000.00'},
                {'method': 'TRANSFER', 'amount': '2000.00'},
            ],
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(set(SalePayment.objects.values_list('method', flat=True)), {'GIFT', 'CARD', 'TRANSFER'})

    def test_unissued_expired_and_blocked_cards_cannot_pay(self):
        self.assertEqual(self.checkout().status_code, 400)
        self.activate()
        self.card.expires_at = timezone.now() - timedelta(seconds=1)
        self.card.save(update_fields=['expires_at'])
        self.assertEqual(self.checkout(key='gift-expired-123456').status_code, 400)
        self.card.expires_at = timezone.now() + timedelta(days=1)
        self.card.is_blocked = True
        self.card.save(update_fields=['expires_at', 'is_blocked'])
        self.assertEqual(self.checkout(key='gift-blocked-123456').status_code, 400)
        self.assertEqual(SaleOrder.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, Decimal('10.000'))

    def test_insufficient_card_balance_rolls_back_stock(self):
        self.activate()
        response = self.checkout(key='gift-overdraw-123456', quantity='2', paid_amount='14000.00')
        self.assertEqual(response.status_code, 400)
        self.card.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.card.balance, Decimal('10000.00'))
        self.assertEqual(self.product.stock_qty, Decimal('10.000'))
        self.assertFalse(SaleOrder.objects.exists())

    def test_activation_requires_payment_confirmation(self):
        self.client.post('/sales/gift-cards/', {'code': self.code, 'payment_method': 'CASH'})
        self.card.refresh_from_db()
        self.assertIsNone(self.card.activated_at)

    def test_lookup_requires_login_and_csrf(self):
        anonymous = Client()
        response = anonymous.post('/sales/api/gift-cards/lookup/', {'code': self.code}, content_type='application/json')
        self.assertEqual(response.status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post('/sales/api/gift-cards/lookup/', {'code': self.code}, content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_raw_barcode_is_not_stored_in_card_or_audit(self):
        self.activate()
        self.card.refresh_from_db()
        self.assertNotEqual(self.card.code_hash, self.code)
        self.assertNotIn(self.code, repr(self.card.__dict__))
        from analytics.models import AuditLog
        self.assertFalse(AuditLog.objects.filter(description__contains=self.code).exists())

    def test_gift_activity_is_visible_on_dashboard_and_csv(self):
        self.activate()
        response = self.checkout(
            key='gift-report-123456', quantity='2', payment_method='MIXED',
            paid_amount='14000.00', payments=[
                {'method': 'GIFT', 'amount': '10000.00'},
                {'method': 'TRANSFER', 'amount': '4000.00'},
            ],
        )
        self.assertEqual(response.status_code, 201, response.content)

        admin = User.objects.create_user(username='gift-report-admin', role=User.Role.ADMIN)
        self.client.force_login(admin)
        dashboard = self.client.get('/')
        self.assertEqual(dashboard.status_code, 200)
        gift_report = dashboard.context['report']['gift_cards']
        self.assertEqual(gift_report['activated_count'], 1)
        self.assertEqual(gift_report['redeemed_amount'], Decimal('10000.00'))

        export = self.client.get(reverse('analytics_export_csv'))
        self.assertEqual(export.status_code, 200)
        body = export.content.decode('utf-8-sig')
        self.assertIn('Подарочные сертификаты', body)
        self.assertIn('Сертификат: 10000.00 ₸ + Kaspi QR: 4000.00 ₸', body)
        self.assertIn('••••BCDE: 10000.00 ₸', body)

    def test_admin_can_add_block_and_delete_only_unused_cards(self):
        admin = User.objects.create_superuser(username='gift-superadmin', password='test-pass')
        self.client.force_login(admin)
        add_url = reverse('admin:sales_giftcard_add')
        response = self.client.post(add_url, {
            'barcode': 'DKR-ABCDE-00001',
            'nominal': '20000.00',
            '_save': 'Сохранить',
        })
        self.assertEqual(response.status_code, 302, response.content)
        added = GiftCard.objects.get(code_hash=code_digest('DKR-ABCDE-00001'))
        self.assertEqual(added.code_last4, '0001')
        self.assertEqual(added.state, 'UNISSUED')

        list_url = reverse('admin:sales_giftcard_changelist')
        response = self.client.get(list_url)
        self.assertContains(response, 'Действия')
        self.assertContains(response, 'Управлять')
        self.assertContains(response, 'Удалить')

        change_url = reverse('admin:sales_giftcard_change', args=[added.pk])
        response = self.client.post(change_url, {'is_blocked': 'on', '_save': 'Сохранить'})
        self.assertEqual(response.status_code, 302, response.content)
        added.refresh_from_db()
        self.assertTrue(added.is_blocked)

        delete_url = reverse('admin:sales_giftcard_delete', args=[added.pk])
        self.assertEqual(self.client.post(delete_url, {'post': 'yes'}).status_code, 302)
        self.assertFalse(GiftCard.objects.filter(pk=added.pk).exists())

        self.activate()
        protected_url = reverse('admin:sales_giftcard_delete', args=[self.card.pk])
        self.assertEqual(self.client.get(protected_url).status_code, 403)
        self.assertContains(self.client.get(list_url), 'История защищена')

    def test_admin_rejects_invalid_or_duplicate_gift_barcode(self):
        admin = User.objects.create_superuser(username='gift-validation-admin', password='test-pass')
        self.client.force_login(admin)
        add_url = reverse('admin:sales_giftcard_add')
        invalid = self.client.post(add_url, {
            'barcode': 'not-a-card', 'nominal': '10000.00', '_save': 'Сохранить',
        })
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, 'Неверный формат штрихкода сертификата')
        duplicate = self.client.post(add_url, {
            'barcode': self.code, 'nominal': '10000.00', '_save': 'Сохранить',
        })
        self.assertEqual(duplicate.status_code, 200)
        self.assertContains(duplicate, 'Такой сертификат уже есть в реестре')
