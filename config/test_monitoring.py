import json
from pathlib import Path
import sqlite3
import tempfile
import time
from unittest.mock import MagicMock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from django.urls import reverse

from config.monitoring import get_metrics


def measurement(timestamp=None):
    return {
        'timestamp': timestamp if timestamp is not None else time.time(),
        'cpu_percent': 12, 'memory_percent': 40,
        'network_rx': 100, 'network_tx': 20, '_counters': {'boot': 1},
    }


@override_settings(TELEGRAM_ALERTS_ENABLED=False)
class MetricsStorageTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'monitor' / 'metrics.sqlite3'
        self.override = override_settings(MONITOR_DB_PATH=self.path)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_shared_sample_and_private_counters(self):
        with patch('config.monitoring._collect', return_value=measurement()) as collect:
            one = get_metrics(history=True)
            two = get_metrics()
        self.assertEqual(collect.call_count, 1)
        self.assertEqual(one['sample'], two['sample'])
        self.assertNotIn('_counters', one['sample'])
        self.assertNotIn('history', two)
        self.assertEqual(len(one['history']), 1)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_threshold_alerts_are_persistent_and_recover(self):
        stamp = time.time()
        high = {**measurement(stamp), 'cpu_percent': 96, 'memory_percent': 91,
                'disk_percent': 88, 'database_ok': False}
        with patch('config.monitoring._collect', return_value=high):
            result = get_metrics(history=True)
        self.assertEqual({item['fingerprint'] for item in result['alerts']},
                         {'cpu_high', 'memory_high', 'disk_high', 'database_down'})
        self.assertTrue(all(item['active'] == 1 for item in result['alerts']))

        recovered = {**measurement(stamp + 61), 'database_ok': True}
        with patch('config.monitoring.time.time', return_value=stamp + 61), \
                patch('config.monitoring._collect', return_value=recovered):
            result = get_metrics(history=True)
        self.assertTrue(any(item['fingerprint'] == 'database_down' and not item['active']
                            for item in result['alerts']))
        self.assertFalse(any(item['fingerprint'] == 'database_down' and item['active']
                             for item in result['alerts']))

    @override_settings(TELEGRAM_ALERTS_ENABLED=True, TELEGRAM_BOT_TOKEN='test-token',
                       TELEGRAM_CHAT_ID='123')
    def test_telegram_sends_only_new_and_recovered_alerts(self):
        stamp = time.time()
        high = {**measurement(stamp), 'cpu_percent': 96}
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch('config.monitoring.urlrequest.urlopen', return_value=response) as send, \
                patch('config.monitoring._collect', return_value=high):
            get_metrics()
            get_metrics()
        self.assertEqual(send.call_count, 1)
        recovered = {**measurement(stamp + 61), 'cpu_percent': 12}
        with patch('config.monitoring.urlrequest.urlopen', return_value=response) as send, \
                patch('config.monitoring.time.time', return_value=stamp + 61), \
                patch('config.monitoring._collect', return_value=recovered):
            get_metrics()
        self.assertEqual(send.call_count, 1)

    def test_alert_store_is_separate_from_business_database(self):
        with patch('config.monitoring._collect', return_value=measurement()):
            get_metrics()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM alerts').fetchone()[0], 0)

    def test_one_history_row_per_minute_and_retention(self):
        stamp = 1800000020.0
        with patch('config.monitoring.time.time', return_value=stamp), patch('config.monitoring._collect', return_value=measurement(stamp)):
            get_metrics()
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO history VALUES (?, ?)', (int(stamp // 60) - 1441, json.dumps(measurement(stamp - 86500))))
        with patch('config.monitoring.time.time', return_value=stamp + 11), patch('config.monitoring._collect', return_value=measurement(stamp + 11)):
            result = get_metrics(history=True)
        self.assertEqual(len(result['history']), 1)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM history').fetchone()[0], 1)

    def test_new_minute_adds_history(self):
        stamp = time.time()
        for offset in (0, 60):
            with patch('config.monitoring.time.time', return_value=stamp + offset), patch('config.monitoring._collect', return_value=measurement(stamp + offset)):
                result = get_metrics(history=True)
        self.assertEqual(len(result['history']), 2)

    def test_failed_sample_rolls_back(self):
        stamp = time.time() - 20
        with patch('config.monitoring._collect', return_value=measurement(stamp)):
            get_metrics()
        with patch('config.monitoring._collect', side_effect=OSError('failed')):
            with self.assertRaises(OSError):
                get_metrics()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(json.loads(db.execute('SELECT payload FROM latest').fetchone()[0])['timestamp'], stamp)

    def test_busy_collector_returns_old_sample(self):
        stamp = time.time() - 20
        with patch('config.monitoring._collect', return_value=measurement(stamp)):
            get_metrics()
        db = sqlite3.connect(self.path)
        try:
            db.execute('BEGIN IMMEDIATE')
            with patch('config.monitoring._collect') as collect:
                self.assertEqual(get_metrics()['sample']['timestamp'], stamp)
                collect.assert_not_called()
        finally:
            db.rollback()
            db.close()


class MonitorAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model()
        cls.owner = user.objects.create_user(username='monitor_owner', is_staff=True, is_superuser=True)
        cls.staff = user.objects.create_user(username='monitor_staff', is_staff=True, role='ADMIN')
        cls.cashier = user.objects.create_user(username='monitor_cashier')

    def test_anonymous_and_cashier_do_not_collect(self):
        for user in (None, self.cashier):
            if user:
                self.client.force_login(user)
            with patch('config.monitoring.get_metrics') as collect:
                self.assertEqual(self.client.get(reverse('admin:server_monitor_data')).status_code, 302)
                collect.assert_not_called()

    def test_staff_cannot_access_monitor(self):
        self.client.force_login(self.staff)
        for url in ('admin:server_monitor', 'admin:server_monitor_data'):
            with patch('config.monitoring.get_metrics') as collect:
                self.assertEqual(self.client.get(reverse(url)).status_code, 403)
                collect.assert_not_called()
        self.assertNotContains(self.client.get(reverse('admin:index')), reverse('admin:server_monitor'))

    def test_owner_page_and_original_admin_models_preserved(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse('admin:index'))
        self.assertContains(response, reverse('admin:server_monitor'))
        for name in ('analytics_auditlog', 'catalog_brand', 'catalog_stockmovement', 'catalog_category', 'catalog_product', 'sales_saleorderitem', 'sales_saleorder', 'users_user', 'auth_group'):
            self.assertContains(response, reverse(f'admin:{name}_changelist'))
        for name in ('catalog_product', 'catalog_brand', 'sales_saleorder', 'users_user', 'auth_group'):
            self.assertContains(response, reverse(f'admin:{name}_add'))
        self.assertContains(response, 'Последние действия')
        self.assertContains(self.client.get(reverse('admin:server_monitor')), 'resources-chart')

    def test_api_is_read_only_and_not_cached(self):
        self.client.force_login(self.owner)
        url = reverse('admin:server_monitor_data')
        with patch('config.monitoring.get_metrics', return_value={'sample': measurement()}) as collect:
            response = self.client.get(url + '?history=1')
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response['Cache-Control'])
            collect.assert_called_once_with(history=True)
        with patch('config.monitoring.get_metrics') as collect:
            self.assertEqual(self.client.post(url).status_code, 405)
            collect.assert_not_called()

    def test_storage_failure_does_not_expose_details(self):
        self.client.force_login(self.owner)
        with patch('config.monitoring.get_metrics', side_effect=OSError('/private/secret')), self.assertLogs('config.admin_site', level='ERROR'):
            response = self.client.get(reverse('admin:server_monitor_data'))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('/private/secret', response.content.decode())

    def test_real_sample_no_business_writes_and_unknown_first_cpu(self):
        from config.monitoring import _collect
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as queries:
            sample = _collect({})
        self.assertIsNone(sample['cpu_percent'])
        self.assertIsNone(sample['network_rx'])
        self.assertTrue(sample['database_ok'])
        self.assertGreater(sample['memory_total'], 0)
        self.assertEqual([q['sql'] for q in queries], ['SELECT 1'])

    def test_audit_existing_add_restriction_preserved(self):
        from analytics.models import AuditLog
        from django.test import RequestFactory
        request = RequestFactory().get('/admin/')
        request.user = self.owner
        self.assertFalse(admin.site._registry[AuditLog].has_add_permission(request))


@override_settings(TELEGRAM_BOT_TOKEN='test-token', TELEGRAM_CHAT_ID='855861024',
                   TELEGRAM_WEBHOOK_SECRET='webhook-secret')
class TelegramMonitorTests(TestCase):
    def _post(self, payload, secret='webhook-secret', chat_id='855861024'):
        return self.client.post(
            reverse('telegram_monitor_webhook', kwargs={'secret': secret}),
            data=json.dumps(payload), content_type='application/json',
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret,
        )

    def test_start_command_is_read_only_and_replies_with_keyboard(self):
        update = {'message': {'chat': {'id': 855861024}, 'text': '/start'}}
        with patch('config.telegram_monitor._api', return_value=True) as api:
            response = self._post(update)
        self.assertEqual(response.status_code, 200)
        api.assert_called_once()
        self.assertEqual(api.call_args.args[0], 'sendMessage')
        self.assertIn('reply_markup', api.call_args.args[1])

    def test_wrong_secret_or_chat_cannot_trigger_bot_actions(self):
        update = {'message': {'chat': {'id': 999}, 'text': '/status'}}
        with patch('config.telegram_monitor._api') as api:
            self.assertEqual(self._post(update, secret='wrong').status_code, 404)
            self.assertEqual(self._post(update).status_code, 200)
        api.assert_not_called()
