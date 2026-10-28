from django.test import TestCase
from django.urls import reverse

from sales.models import SaleOrder
from users.models import User


class FinancialAdminSafetyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username='finance-admin', password='strong-test-password', role=User.Role.ADMIN
        )
        cls.order = SaleOrder.objects.create(order_number='ADMIN-SAFE-001', cashier=cls.admin)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_receipt_is_viewable_but_not_editable_or_deletable(self):
        change_url = reverse('admin:sales_saleorder_change', args=[self.order.pk])
        response = self.client.get(change_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.order.order_number)
        self.assertNotContains(response, 'name="_save"')
        self.assertEqual(
            self.client.get(reverse('admin:sales_saleorder_delete', args=[self.order.pk])).status_code,
            403,
        )
        self.assertEqual(self.client.get(reverse('admin:sales_saleorder_add')).status_code, 403)
