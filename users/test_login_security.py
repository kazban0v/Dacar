import concurrent.futures
from pathlib import Path
import secrets
import sqlite3
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings

from config.security_settings import security_settings
from users.login_throttle import client_ip, release_success, reserve


class PrivateStorageMixin:
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'security' / 'login.sqlite3'
        config = override_settings(LOGIN_THROTTLE_DB_PATH=self.path,
            LOGIN_THROTTLE_PAIR_LIMIT=5, LOGIN_THROTTLE_IP_LIMIT=60,
            LOGIN_THROTTLE_WINDOW=300, LOGIN_TRUSTED_PROXY_IPS=())
        config.enable()
        self.addCleanup(config.disable)


class LoginBudgetTests(PrivateStorageMixin, SimpleTestCase):
    def test_five_attempts_then_temporary_block(self):
        with patch('users.login_throttle.time.time', return_value=1000):
            for _ in range(5):
                self.assertEqual(reserve('192.0.2.1', 'alice')[0], 0)
            self.assertEqual(reserve('192.0.2.1', 'alice'), (300, None))
        with patch('users.login_throttle.time.time', return_value=1100):
            self.assertEqual(reserve('192.0.2.1', 'alice')[0], 200)
        with patch('users.login_throttle.time.time', return_value=1300):
            self.assertEqual(reserve('192.0.2.1', 'alice')[0], 0)

    def test_other_cashier_same_ip_and_same_account_other_ip_allowed(self):
        for _ in range(5):
            reserve('192.0.2.1', 'alice')
        self.assertEqual(reserve('192.0.2.1', 'bob')[0], 0)
        self.assertEqual(reserve('192.0.2.2', 'alice')[0], 0)

    def test_ip_budget_stops_username_spraying(self):
        with override_settings(LOGIN_THROTTLE_IP_LIMIT=10):
            for i in range(10):
                self.assertEqual(reserve('192.0.2.1', f'user{i}')[0], 0)
            self.assertGreater(reserve('192.0.2.1', 'another')[0], 0)

    def test_success_resets_pair_but_not_other_users_failures(self):
        with override_settings(LOGIN_THROTTLE_IP_LIMIT=6):
            for _ in range(4):
                reserve('192.0.2.1', 'alice')
            ticket = reserve('192.0.2.1', 'bob')[1]
            release_success(ticket)
            self.assertEqual(reserve('192.0.2.1', 'bob')[0], 0)
            self.assertEqual(reserve('192.0.2.1', 'carol')[0], 0)
            self.assertGreater(reserve('192.0.2.1', 'dave')[0], 0)

    def test_parallel_requests_share_atomic_budget(self):
        # Initialize schema outside concurrent workers; admissions remain atomic.
        release_success(reserve('192.0.2.1', 'alice')[1])
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: reserve('192.0.2.1', 'alice')[0], range(12)))
        self.assertEqual(results.count(0), 5)

    def test_storage_private_and_raw_identifiers_not_saved(self):
        reserve('192.0.2.123', 'unique_cashier_name')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(b'192.0.2.123', self.path.read_bytes())
        self.assertNotIn(b'unique_cashier_name', self.path.read_bytes())

    def test_proxy_headers_ignored_unless_immediate_peer_trusted(self):
        r = RequestFactory().post('/users/login/', REMOTE_ADDR='192.0.2.1', HTTP_X_REAL_IP='198.51.100.1', HTTP_X_FORWARDED_FOR='203.0.113.1')
        self.assertEqual(client_ip(r), '192.0.2.1')
        with override_settings(LOGIN_TRUSTED_PROXY_IPS=('192.0.2.1',)):
            self.assertEqual(client_ip(r), '198.51.100.1')
            r.META['HTTP_X_REAL_IP'] = 'invalid'
            self.assertEqual(client_ip(r), '192.0.2.1')

    def test_expired_rows_removed(self):
        with patch('users.login_throttle.time.time', return_value=1000):
            reserve('192.0.2.1', 'old')
        with patch('users.login_throttle.time.time', return_value=1400):
            reserve('192.0.2.2', 'new')
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0], 2)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class LoginFlowTests(PrivateStorageMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model()
        cls.cashier = user.objects.create_user(username='cashier', password='correct pass')
        cls.owner = user.objects.create_superuser(username='owner', password='correct pass')
        user.objects.create_user(username='spaced', password=' pass with spaces ')

    def test_desktop_mobile_admin_share_budget(self):
        paths = ['/users/login/', '/m/users/login/', '/admin/login/']
        for i in range(5):
            response = self.client.post(paths[i % 3], {'username': 'unknown', 'password': 'wrong'})
            self.assertEqual(response.status_code, 200)
        response = self.client.post('/admin/login/', {'username': 'unknown', 'password': 'wrong'})
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertContains(response, 'Попробуйте чуть позже', status_code=429)

    def test_successful_login_all_entrypoints(self):
        for path, name in [('/users/login/', 'cashier'), ('/m/users/login/', 'cashier'), ('/admin/login/', 'owner')]:
            with self.subTest(path=path):
                client = Client()
                response = client.post(path, {'username': name, 'password': 'correct pass'})
                self.assertEqual(response.status_code, 302)
                self.assertIn('_auth_user_id', client.session)
                if path.startswith('/m/'):
                    self.assertEqual(response.url, '/m/')

    def test_success_clears_previous_failures(self):
        for _ in range(4):
            self.client.post('/users/login/', {'username': 'cashier', 'password': 'wrong'})
        self.assertEqual(self.client.post('/users/login/', {'username': 'cashier', 'password': 'correct pass'}).status_code, 302)
        self.client.logout()
        for _ in range(5):
            self.assertEqual(self.client.post('/users/login/', {'username': 'cashier', 'password': 'wrong'}).status_code, 200)

    def test_password_whitespace_not_removed(self):
        response = self.client.post('/users/login/', {'username': 'spaced', 'password': ' pass with spaces '})
        self.assertEqual(response.status_code, 302)
        self.assertIn('_auth_user_id', self.client.session)

    def test_authenticated_cashier_cannot_bypass_admin_throttle(self):
        self.client.force_login(self.cashier)
        for _ in range(5):
            self.client.post('/admin/login/', {'username': 'owner', 'password': 'wrong'})
        self.assertEqual(self.client.post('/admin/login/', {'username': 'owner', 'password': 'wrong'}).status_code, 429)

    def test_get_does_not_create_storage(self):
        self.assertEqual(self.client.get('/users/login/').status_code, 200)
        self.assertFalse(self.path.exists())

    def test_csrf_checked_before_reservation(self):
        client = Client(enforce_csrf_checks=True)
        with patch('users.login_throttle.reserve') as reserve_mock:
            response = client.post('/users/login/', {'username': 'cashier', 'password': 'correct pass'})
            self.assertEqual(response.status_code, 403)
            reserve_mock.assert_not_called()

    def test_limiter_unavailable_fails_closed_without_password_check(self):
        with patch('users.login_throttle.reserve', side_effect=OSError('private path')), patch('users.views.authenticate') as authenticate, self.assertLogs('users.login_throttle', level='ERROR'):
            response = self.client.post('/users/login/', {'username': 'cashier', 'password': 'correct pass'})
        self.assertEqual(response.status_code, 503)
        authenticate.assert_not_called()
        self.assertNotIn('private path', response.content.decode())

    def test_unknown_host_rejected(self):
        with override_settings(ALLOWED_HOSTS=['dacar-market.kz']):
            self.assertEqual(self.client.get('/users/login/', HTTP_HOST='attacker.example').status_code, 400)

    def test_production_https_cookies_and_local_http(self):
        prod = security_settings({'DJANGO_SECRET_KEY': secrets.token_urlsafe(64)})
        with override_settings(**prod):
            client = Client(enforce_csrf_checks=True)
            response = client.get('/users/login/', HTTP_HOST='dacar-market.kz', secure=True)
            self.assertTrue(response.cookies['csrftoken']['secure'])
            response = client.post('/users/login/', {'username': 'cashier', 'password': 'correct pass', 'csrfmiddlewaretoken': response.cookies['csrftoken'].value}, HTTP_HOST='dacar-market.kz', HTTP_ORIGIN='https://dacar-market.kz', secure=True)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.cookies['sessionid']['secure'])
            self.assertTrue(response.cookies['sessionid']['httponly'])
            self.assertEqual(client.get('/users/login/', HTTP_HOST='dacar-market.kz').status_code, 301)
        local = security_settings({'DJANGO_ENV': 'development', 'DJANGO_SECRET_KEY': secrets.token_urlsafe(64)})
        with override_settings(**local):
            response = Client().post('/users/login/', {'username': 'cashier', 'password': 'correct pass'}, HTTP_HOST='localhost')
            self.assertEqual(response.status_code, 302)
            self.assertFalse(response.cookies['sessionid']['secure'])
