from datetime import date, datetime, time, timedelta
from decimal import Decimal
import uuid

from django.test import TestCase
from django.utils import timezone

from catalog.models import Brand, Product, StockMovement
from sales.documents import weekly_shine_report, allocate_amount
from sales.models import SaleOrder, SaleOrderItem, CompanyInvoice
from users.models import User


class DocumentsAndPaymentsTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='doc-admin', role='ADMIN')
        self.cashier = User.objects.create_user(username='doc-cashier', role='CASHIER')
        self.other = User.objects.create_user(username='doc-other', role='CASHIER')
        self.brand = Brand.objects.create(name='Shine Systems', slug='shine-systems')
        self.product = Product.objects.create(name='Shine Systems InteriorCleaner 1 л',
            sku='SH-01', barcode='SH-01', brand=self.brand, retail_price=100, purchase_price=40, stock_qty=20)
        self.client.force_login(self.admin)

    def checkout_data(self, **kwargs):
        return {'client_sync_id':uuid.uuid4().hex, 'items':[{'product_id':self.product.pk, 'quantity':'2'}],
                'payment_method':'MIXED', 'paid_amount':'200',
                'payments':[{'method':'CASH','amount':'50'}, {'method':'CARD','amount':'60'}, {'method':'TRANSFER','amount':'90'}], **kwargs}

    def test_mixed_payment_and_retry(self):
        payload = self.checkout_data()
        first = self.client.post('/sales/api/checkout/', payload, content_type='application/json')
        self.assertEqual(first.status_code, 201, first.content)
        second = self.client.post('/sales/api/checkout/', payload, content_type='application/json')
        self.assertEqual(second.status_code, 200)
        order = SaleOrder.objects.get()
        self.assertEqual(order.payments.count(), 3)
        self.assertEqual(sum(p.amount for p in order.payments.all()), order.total_amount)
        self.assertEqual(order.items.get().product_name_snapshot, self.product.name)
        self.client.post(f'/sales/orders/{order.pk}/refund/', {'refund_reason':'Возврат'})
        self.assertEqual(order.payments.count(), 3)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 20)

    def test_invalid_splits_do_not_post_sale(self):
        cases = [[], [{'method':'CASH','amount':'200'}],
            [{'method':'CASH','amount':'100'},{'method':'CASH','amount':'100'}],
            [{'method':'CASH','amount':'100'},{'method':'TRANSFER','amount':'99.99'}],
            [{'method':'CASH','amount':'-10'},{'method':'CARD','amount':'210'}],
            [{'method':'CASH','amount':'100'},{'method':'MIXED','amount':'100'}]]
        for parts in cases:
            response=self.client.post('/sales/api/checkout/', self.checkout_data(payments=parts), content_type='application/json')
            self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(SaleOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_qty, 20)

    def test_analytics_uses_payment_parts(self):
        from analytics.views import _analytics_report
        self.client.post('/sales/api/checkout/', self.checkout_data(), content_type='application/json')
        report = _analytics_report(timezone.localdate(), timezone.localdate())
        amounts = {row['payment_method']:row['amount'] for row in report['payment_rows']}
        self.assertEqual(amounts, {'CASH':Decimal('50'), 'CARD':Decimal('60'), 'TRANSFER':Decimal('90')})
        page = self.client.get('/sales/orders/?payment_method=CARD')
        self.assertEqual(len(page.context['orders']), 1)

    def test_zero_stock_filter_and_pagination(self):
        for i in range(17):
            Product.objects.create(name=f'Zero {i}', sku=f'zero-{i}', barcode=f'zero-{i}', stock_qty=0)
        Product.objects.create(name='Low', sku='low', barcode='low', stock_qty=1)
        Product.objects.create(name='Negative', sku='neg', barcode='neg', stock_qty=-1)
        for path in ['/catalog/', '/m/catalog/']:
            page = self.client.get(path, {'out_of_stock':1, 'page':2})
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.context['page_obj'].paginator.count, 17)
            self.assertTrue(all(p.stock_qty == 0 for p in page.context['products']))
            self.assertContains(page, 'out_of_stock=1')

    def event_order(self, day, status='COMPLETED', refund_day=None, amount='180'):
        order = SaleOrder.objects.create(order_number=uuid.uuid4().hex, cashier=self.admin,
            status=status, total_amount=amount, subtotal_amount=200, discount_amount=20)
        stamp = lambda d: timezone.make_aware(datetime.combine(d, time.min))
        SaleOrder.objects.filter(pk=order.pk).update(created_at=stamp(day), refunded_at=stamp(refund_day) if refund_day else None)
        SaleOrderItem.objects.create(order=order, product=self.product, quantity=2,
            product_name_snapshot=self.product.name, brand_name_snapshot=self.brand.name,
            sku_snapshot=self.product.sku, unit_snapshot='шт', purchase_price_snapshot=40,
            unit_price=100, total_amount=200)
        return order

    def test_week_boundaries_refunds_and_historical_name(self):
        monday=date(2026,9,14)
        self.event_order(monday, status='REFUNDED', refund_day=monday+timedelta(days=8))
        self.event_order(monday-timedelta(days=1), status='REFUNDED', refund_day=monday+timedelta(days=1))
        self.event_order(monday+timedelta(days=7))  # Excluded next Monday.
        self.event_order(monday, status='CANCELLED')
        self.product.name='Changed later'; self.product.save()
        report=weekly_shine_report(monday)
        self.assertEqual(len(report['rows']), 1)
        row=report['rows'][0]
        self.assertEqual(row['name'], 'Shine Systems InteriorCleaner 1 л')
        self.assertEqual(row['sold_qty'], 2)
        self.assertEqual(row['returned_qty'], 2)
        self.assertEqual(report['sold_total'], Decimal('180'))
        self.assertEqual(report['net_total'], 0)

    def test_discount_allocation_across_brands(self):
        order=self.event_order(date(2026,9,14), amount='270')
        other=Product.objects.create(name='Other', sku='other', barcode='other')
        SaleOrderItem.objects.create(order=order, product=other, quantity=1, purchase_price_snapshot=0,
                                     unit_price=100, total_amount=100)
        report=weekly_shine_report(date(2026,9,14))
        self.assertEqual(report['net_total'], Decimal('180'))
        self.assertEqual(len(report['events']), 1)
        values=allocate_amount(Decimal('0.02'), [Decimal(1)]*3)
        self.assertEqual(sum(values), Decimal('.02'))
        self.assertEqual(values, [Decimal('.01'), Decimal('.01'), Decimal('0')])

    def invoice_data(self):
        return dict(key=str(uuid.uuid4()), buyer_name='ТОО Покупатель', buyer_bin='123456789012',
                    buyer_address='Актобе, улица Тестовая, 12', items=[{'product_id':self.product.pk,'quantity':'2'}])

    def test_invoice_is_independent_idempotent_and_snapshot(self):
        data=self.invoice_data()
        first=self.client.post('/sales/api/invoices/', data, content_type='application/json')
        self.assertEqual(first.status_code, 201, first.content)
        again=self.client.post('/sales/api/invoices/', data, content_type='application/json')
        self.assertEqual(again.json(), first.json())
        invoice=CompanyInvoice.objects.get()
        self.assertEqual(invoice.seller['bin'], '050810551773')
        self.assertEqual(invoice.seller['tax'], 'Без НДС')
        self.assertEqual(invoice.total_amount, Decimal('200'))
        self.assertFalse(SaleOrder.objects.exists())
        self.assertFalse(StockMovement.objects.exists())
        self.product.refresh_from_db(); self.assertEqual(self.product.stock_qty, 20)
        self.product.name='Changed'; self.product.retail_price=500; self.product.save()
        self.assertEqual(invoice.lines[0]['name'], 'Shine Systems InteriorCleaner 1 л')
        pdf=self.client.get(first.json()['url'])
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b'%PDF-'))
        conflict=self.client.post('/sales/api/invoices/', {**data, 'buyer_name':'Another'}, content_type='application/json')
        self.assertEqual(conflict.status_code, 409)

    def test_invoice_and_report_permissions(self):
        self.client.force_login(self.cashier)
        invoice=self.client.post('/sales/api/invoices/', self.invoice_data(), content_type='application/json')
        self.assertEqual(invoice.status_code, 201)
        self.assertEqual(self.client.get('/sales/reports/shine/').status_code, 403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(invoice.json()['url']).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get('/sales/invoices/').status_code, 302)
        self.assertEqual(self.client.post('/sales/api/invoices/', self.invoice_data(), content_type='application/json').status_code, 403)

    def test_invalid_invoice_and_week(self):
        data=self.invoice_data()
        data['buyer_bin']='abc'
        self.assertEqual(self.client.post('/sales/api/invoices/', data, content_type='application/json').status_code, 400)
        self.assertEqual(self.client.get('/sales/reports/shine/?week=2026-09-15').status_code, 400)
        self.assertEqual(self.client.get('/sales/reports/shine/?week=2026-09-14&format=pdf').status_code, 200)
        self.assertFalse(CompanyInvoice.objects.exists())
