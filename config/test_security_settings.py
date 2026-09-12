import secrets
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from config.security_settings import security_settings


class SecuritySettingsTests(SimpleTestCase):
    def config(self, **values):
        return security_settings({'DJANGO_SECRET_KEY': secrets.token_urlsafe(64), **values})

    def test_production_is_default_and_never_debug(self):
        s = self.config(DJANGO_DEBUG='1')
        self.assertEqual(s['DJANGO_ENV'], 'production')
        self.assertFalse(s['DEBUG'])
        self.assertTrue(s['SESSION_COOKIE_SECURE'])
        self.assertTrue(s['CSRF_COOKIE_SECURE'])
        self.assertTrue(s['SECURE_SSL_REDIRECT'])
        self.assertEqual(s['SECRET_KEY_FALLBACKS'], [])
        self.assertEqual(s['ALLOWED_HOSTS'], ['dacar-market.kz', 'www.dacar-market.kz'])
        self.assertFalse(s['USE_X_FORWARDED_HOST'])

    def test_local_http_allowed(self):
        s = self.config(DJANGO_ENV='development', DJANGO_DEBUG='1')
        self.assertFalse(s['SESSION_COOKIE_SECURE'])
        self.assertFalse(s['CSRF_COOKIE_SECURE'])
        self.assertFalse(s['SECURE_SSL_REDIRECT'])
        self.assertTrue(s['DEBUG'])
        self.assertIn('127.0.0.1', s['ALLOWED_HOSTS'])
        self.assertNotIn('dacar-market.kz', s['ALLOWED_HOSTS'])
        self.assertIsNone(s['SECURE_PROXY_SSL_HEADER'])
        self.assertEqual(s['LOGIN_TRUSTED_PROXY_IPS'], ())

    def test_missing_weak_secret_rejected(self):
        for value in ('', 'short', 'a' * 70, 'django-insecure-' + secrets.token_urlsafe(64)):
            with self.subTest(value=value[:8]), self.assertRaises(ImproperlyConfigured):
                security_settings({'DJANGO_SECRET_KEY': value})

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            self.config(DJANGO_ENV='prodution')

    def test_host_wildcards_and_urls_rejected(self):
        for host in ('*', '.example.com', 'https://example.com', '*.ngrok-free.app', ''):
            with self.subTest(host=host), self.assertRaises(ImproperlyConfigured):
                self.config(DJANGO_ALLOWED_HOSTS=host)

    def test_trusted_origins_explicit_https_on_production(self):
        for origin in ('http://dacar-market.kz', 'https://*.ngrok-free.app'):
            with self.assertRaises(ImproperlyConfigured):
                self.config(DJANGO_CSRF_TRUSTED_ORIGINS=origin)
        self.assertEqual(self.config()['CSRF_TRUSTED_ORIGINS'], [])
        self.assertEqual(self.config(DJANGO_CSRF_TRUSTED_ORIGINS='https://dacar-market.kz')['CSRF_TRUSTED_ORIGINS'], ['https://dacar-market.kz'])
