from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from catalog.models import Category, Product
from sales.models import SaleOrder, SaleOrderItem
from sales.raw_printer import build_escpos_bytes_for_order, send_raw_bytes_to_printer


class ReceiptPrintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.cashier = users.objects.create_user(username='receipt-cashier', role='CASHIER')
        cls.other = users.objects.create_user(username='receipt-other', role='CASHIER')
        cls.admin = users.objects.create_user(username='receipt-admin', role='ADMIN')
        category = Category.objects.create(name='Тест', slug='receipt-test')
        product = Product.objects.create(
            name='<img src=x onerror=alert(1)> Очень длинное название шампуня для автомобиля',
            sku='RECEIPT', barcode='RECEIPT', category=category,
            purchase_price=100, retail_price=Decimal('1250.25'), stock_qty=10,
        )
        cls.order = SaleOrder.objects.create(
            order_number='RECEIPT-0001', cashier=cls.cashier, status='COMPLETED',
            payment_method='CASH', subtotal_amount=Decimal('1250.25'),
            total_amount=Decimal('1250.25'), paid_amount=2000, change_amount=Decimal('749.75'),
        )
        SaleOrderItem.objects.create(order=cls.order, product=product, quantity=1,
                                     purchase_price_snapshot=100, unit_price=Decimal('1250.25'),
                                     total_amount=Decimal('1250.25'))

    def test_shared_receipt_escapes_names_and_preserves_edge_space(self):
        self.client.force_login(self.cashier)
        for prefix in ('', '/m'):
            response = self.client.get(f'{prefix}/sales/orders/{self.order.pk}/print/?autoprint=0')
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, 'desktop/sales/print_receipt.html')
            self.assertContains(response, '&lt;img')
            self.assertNotContains(response, '<img src=x')
            self.assertContains(response, 'padding:6mm 3mm 10mm')
            self.assertContains(response, 'var(--receipt-width,71.9mm)')
            self.assertNotContains(response, 'Math.min(')
            self.assertNotContains(response, 'width: 100% !important')
            self.assertContains(response, '1250,25')

    def test_cashier_cannot_print_other_receipt_in_either_transport(self):
        self.client.force_login(self.other)
        with patch('sales.views.print_order_direct') as printer:
            for prefix in ('', '/m'):
                self.assertEqual(self.client.get(f'{prefix}/sales/orders/{self.order.pk}/print/').status_code, 302)
                self.assertEqual(self.client.post(f'{prefix}/sales/api/orders/{self.order.pk}/print-raw/').status_code, 403)
            printer.assert_not_called()

    def test_admin_can_print_and_refund_has_distinct_label(self):
        self.order.status = 'REFUNDED'
        self.order.refunded_at = timezone.now()
        self.order.refunded_by = self.admin
        self.order.refund_reason = 'Возврат товара'
        self.order.save()
        self.client.force_login(self.admin)
        response = self.client.get(f'/sales/orders/{self.order.pk}/print/?autoprint=0')
        self.assertContains(response, 'ВОЗВРАТ ОФОРМЛЕН')
        self.assertContains(response, 'Дата возврата')
        self.assertNotContains(response, '<span>Сдача</span>')

    def test_escpos_preserves_cents_and_explicit_feed(self):
        raw = build_escpos_bytes_for_order(self.order)
        self.assertIn(b'\x1bJ\x50', raw)
        self.assertTrue(raw.endswith(b'\x1bJ\x78\x1dVA\x00'))
        self.assertIn('1 250,25 тг'.encode('cp866'), raw)
        self.assertIn('749,75 тг'.encode('cp866'), raw)

    @override_settings(THERMAL_PRINT_TRANSPORT='browser')
    def test_tspl_driver_never_receives_escpos_by_default(self):
        with patch('sales.raw_printer.subprocess.run') as spool:
            with self.assertRaises(RuntimeError):
                send_raw_bytes_to_printer(b'test', 'EPT371U')
            spool.assert_not_called()

    @override_settings(THERMAL_PRINT_TRANSPORT='escpos')
    def test_offline_printer_is_an_error_not_mock_success(self):
        with patch('sales.raw_printer.HAS_WIN32PRINT', False), \
             patch('sales.raw_printer.shutil.which', return_value='/usr/bin/tool'), \
             patch('sales.raw_printer._cups') as status, \
             patch('sales.raw_printer.subprocess.run') as spool:
            status.return_value.returncode = 0
            status.return_value.stdout = 'Printer offline.'
            with self.assertRaises(RuntimeError):
                send_raw_bytes_to_printer(b'test', 'EPT371U')
            spool.assert_not_called()
