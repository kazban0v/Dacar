from datetime import datetime, timedelta
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from analytics.views import _analytics_report, _period_comparison
from catalog.models import Category, Product
from sales.models import SaleOrder, SaleOrderItem


User = get_user_model()


class DashboardAnalyticsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='analytics-admin', password='test-password', role=User.Role.ADMIN
        )
        self.cashier = User.objects.create_user(
            username='cashier', password='test-password', role=User.Role.CASHIER
        )
        self.category = Category.objects.create(name='Химия', slug='chemistry')
        self.product = Product.objects.create(
            name='Тестовый шампунь',
            sku='TEST-001',
            barcode='200000000001',
            category=self.category,
            purchase_price=Decimal('5000.00'),
            retail_price=Decimal('9000.00'),
            stock_qty=Decimal('10.000'),
            min_stock_alert=Decimal('2.000'),
        )
        self.completed = self._order(
            'DACAR-TEST-0001',
            SaleOrder.Status.COMPLETED,
            SaleOrder.PaymentMethod.CASH,
            subtotal='10000.00',
            discount='1000.00',
            total='9000.00',
            cost='5000.00',
        )
        self.refunded = self._order(
            'DACAR-TEST-0002',
            SaleOrder.Status.REFUNDED,
            SaleOrder.PaymentMethod.TRANSFER,
            subtotal='5000.00',
            discount='1000.00',
            total='4000.00',
            cost='2000.00',
            refunded_at=timezone.now(),
        )

    def _order(self, number, status, payment, *, subtotal, discount, total, cost, refunded_at=None):
        order = SaleOrder.objects.create(
            order_number=number,
            cashier=self.cashier,
            status=status,
            payment_method=payment,
            subtotal_amount=Decimal(subtotal),
            discount_amount=Decimal(discount),
            total_amount=Decimal(total),
            paid_amount=Decimal(total),
            refunded_at=refunded_at,
        )
        SaleOrderItem.objects.create(
            order=order,
            product=self.product,
            quantity=Decimal('1.000'),
            purchase_price_snapshot=Decimal(cost),
            unit_price=Decimal(total),
            total_amount=Decimal(total),
        )
        return order

    def test_report_uses_gross_sales_and_refunds_as_separate_events(self):
        today = timezone.localdate()
        report = _analytics_report(today, today)

        self.assertEqual(report['gross_revenue'], Decimal('13000'))
        self.assertEqual(report['refund_amount'], Decimal('4000'))
        self.assertEqual(report['net_revenue'], Decimal('9000'))
        self.assertEqual(report['net_cost'], Decimal('5000'))
        self.assertEqual(report['gross_profit'], Decimal('4000'))
        self.assertEqual(report['check_count'], 2)
        self.assertEqual(report['refund_count'], 1)
        self.assertEqual(report['cohort_refund_count'], 1)
        self.assertEqual(report['prior_period_refund_count'], 0)
        self.assertEqual(report['refund_rate'], Decimal('50'))
        self.assertEqual(report['average_check'], Decimal('6500'))
        self.assertEqual(report['net_units'], Decimal('1'))
        self.assertTrue(report['margin_available'])
        self.assertFalse(report['returns_exceed_sales'])

    def test_old_receipt_refund_is_explained_without_impossible_rate_or_margin(self):
        old_refund = self._order(
            'DACAR-OLD-REFUND-0001',
            SaleOrder.Status.REFUNDED,
            SaleOrder.PaymentMethod.CARD,
            subtotal='20000.00',
            discount='0.00',
            total='20000.00',
            cost='10000.00',
            refunded_at=timezone.now(),
        )
        SaleOrder.objects.filter(pk=old_refund.pk).update(
            created_at=timezone.now() - timedelta(days=30)
        )

        today = timezone.localdate()
        report = _analytics_report(today, today)

        self.assertEqual(report['gross_revenue'], Decimal('13000'))
        self.assertEqual(report['refund_amount'], Decimal('24000'))
        self.assertEqual(report['net_revenue'], Decimal('-11000'))
        self.assertEqual(report['refund_count'], 2)
        self.assertEqual(report['cohort_refund_count'], 1)
        self.assertEqual(report['prior_period_refund_count'], 1)
        self.assertEqual(report['prior_period_refund_amount'], Decimal('20000'))
        self.assertEqual(report['refund_rate'], Decimal('50'))
        self.assertTrue(report['returns_exceed_sales'])
        self.assertFalse(report['margin_available'])
        self.assertIsNone(report['margin'])

        self.client.force_login(self.admin)
        dashboard = self.client.get(reverse('dashboard'), {'period': 'today'})
        self.assertContains(dashboard, 'Возвраты превысили продажи выбранного периода')
        self.assertContains(dashboard, 'По более ранним чекам: 1')
        self.assertContains(dashboard, 'Маржа не рассчитывается')

    def test_inventory_is_valued_at_purchase_and_retail_prices(self):
        report = _analytics_report(timezone.localdate(), timezone.localdate())
        inventory = report['inventory']

        self.assertEqual(inventory['stock_cost'], Decimal('50000'))
        self.assertEqual(inventory['stock_retail'], Decimal('90000'))
        self.assertEqual(inventory['potential_profit'], Decimal('40000'))
        self.assertEqual(inventory['attention_count'], 0)

    def test_chart_api_filters_payments_and_products_by_selected_period(self):
        old = self._order(
            'DACAR-OLD-0001',
            SaleOrder.Status.COMPLETED,
            SaleOrder.PaymentMethod.CARD,
            subtotal='99000.00',
            discount='0.00',
            total='99000.00',
            cost='1000.00',
        )
        old_time = timezone.now() - timedelta(days=30)
        SaleOrder.objects.filter(pk=old.pk).update(created_at=old_time)

        self.client.force_login(self.admin)
        response = self.client.get(reverse('api_analytics_data'), {'period': 'week'})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn('Карта', payload['payment_methods']['labels'])
        self.assertEqual(sum(payload['payment_methods']['values']), 13000.0)
        self.assertEqual(payload['top_products']['values'], [1.0])

    def test_dashboard_and_csv_export_are_available_to_admin(self):
        self.client.force_login(self.admin)
        dashboard = self.client.get(reverse('dashboard'), {'period': 'month'})
        export = self.client.get(reverse('analytics_export_csv'), {'period': 'month'})

        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, 'Чистая выручка')
        self.assertContains(dashboard, 'Качество данных')
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export['Content-Type'], 'text/csv; charset=utf-8')
        csv_text = export.content.decode('utf-8-sig')
        self.assertIn('Основные показатели', csv_text)
        self.assertIn('Склад на текущий момент', csv_text)
        self.assertIn('Способы оплаты', csv_text)

    def test_dashboard_cards_drill_down_with_the_selected_period(self):
        old_refund = self._order(
            'DACAR-OLD-DRILLDOWN',
            SaleOrder.Status.REFUNDED,
            SaleOrder.PaymentMethod.CARD,
            subtotal='20000.00',
            discount='0.00',
            total='20000.00',
            cost='10000.00',
            refunded_at=timezone.now(),
        )
        SaleOrder.objects.filter(pk=old_refund.pk).update(
            created_at=timezone.now() - timedelta(days=30)
        )

        self.client.force_login(self.admin)
        dashboard = self.client.get(reverse('dashboard'), {'period': 'day'})
        drilldown = dashboard.context['drilldown']

        receipts = self.client.get(drilldown['orders'])
        self.assertContains(receipts, self.completed.order_number)
        self.assertContains(receipts, self.refunded.order_number)
        self.assertNotContains(receipts, old_refund.order_number)
        self.assertContains(receipts, timezone.localdate().strftime('%d.%m.%Y'))

        refunds = self.client.get(drilldown['refunds'])
        self.assertContains(refunds, self.refunded.order_number)
        self.assertContains(refunds, old_refund.order_number)
        self.assertNotContains(refunds, self.completed.order_number)
        self.assertContains(refunds, 'По дате возврата')

        cash_row = next(row for row in dashboard.context['report']['payment_rows'] if row['payment_method'] == SaleOrder.PaymentMethod.CASH)
        cash_receipts = self.client.get(cash_row['drilldown_url'])
        self.assertContains(cash_receipts, self.completed.order_number)
        self.assertNotContains(cash_receipts, self.refunded.order_number)

    def test_cashier_cannot_open_management_export(self):
        client = Client()
        client.force_login(self.cashier)
        response = client.get(reverse('analytics_export_csv'))
        self.assertEqual(response.status_code, 403)

    @patch('analytics.ai.Groq')
    def test_ai_insight_uses_prepared_report_and_returns_text(self, groq_cls):
        groq_cls.return_value.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='Итог: продажи стабильны.'))]
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('api_ai_insight') + '?period=day',
            data=json.dumps({'question': 'Сделай краткий итог'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['answer'], 'Итог: продажи стабильны.')
        request_kwargs = groq_cls.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(request_kwargs['model'], 'openai/gpt-oss-120b')
        self.assertIn('Подготовленные данные Django', request_kwargs['messages'][1]['content'])

    def test_period_comparison_marks_growth_and_cost_growth_correctly(self):
        revenue = _period_comparison(Decimal('120'), Decimal('100'))
        refunds = _period_comparison(Decimal('120'), Decimal('100'), lower_is_better=True)
        no_baseline = _period_comparison(Decimal('50'), Decimal('0'))

        self.assertEqual(revenue['text'], '+20.0%')
        self.assertEqual(revenue['tone'], 'good')
        self.assertEqual(refunds['tone'], 'bad')
        self.assertEqual(no_baseline['text'], 'Новое значение')
