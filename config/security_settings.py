"""Explicit development/production security profiles; no secret fallback."""
from django.core.exceptions import ImproperlyConfigured


def security_settings(env):
    mode = env.get('DJANGO_ENV', 'production').strip().lower()
    if mode not in ('development', 'production'):
        raise ImproperlyConfigured('DJANGO_ENV must be development or production.')
    production = mode == 'production'
    key = env.get('DJANGO_SECRET_KEY', '').strip()
    if len(key) < 50 or len(set(key)) < 5 or key.startswith('django-insecure-'):
        raise ImproperlyConfigured('Set a strong, private DJANGO_SECRET_KEY in the environment (at least 50 characters).')
    default_hosts = 'dacar-market.kz,www.dacar-market.kz' if production else 'localhost,127.0.0.1,[::1],10.0.2.2'
    hosts = [h.strip() for h in env.get('DJANGO_ALLOWED_HOSTS', default_hosts).split(',') if h.strip()]
    if not hosts or any('*' in h or '/' in h or h.startswith('.') for h in hosts):
        raise ImproperlyConfigured('DJANGO_ALLOWED_HOSTS must contain explicit host names, not wildcards or URLs.')
    origins = [h.strip() for h in env.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if h.strip()]
    if any('*' in h or not h.startswith('https://' if production else ('http://', 'https://')) for h in origins):
        raise ImproperlyConfigured('Use explicit trusted origins; production requires HTTPS.')
    return {
        'DJANGO_ENV': mode,
        'SECRET_KEY': key,
        'SECRET_KEY_FALLBACKS': [],
        'DEBUG': not production and env.get('DJANGO_DEBUG', '0') == '1',
        'ALLOWED_HOSTS': hosts,
        # Same-origin forms don't require entries here. Add only actual alternate origins.
        'CSRF_TRUSTED_ORIGINS': origins,
        'SESSION_COOKIE_SECURE': production,
        'CSRF_COOKIE_SECURE': production,
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Lax',
        'CSRF_COOKIE_SAMESITE': 'Lax',
        'SECURE_SSL_REDIRECT': production,
        # Production sits behind Nginx on loopback; deployment must verify it
        # overwrites X-Forwarded-Proto and X-Real-IP. Gunicorn isn't public.
        'SECURE_PROXY_SSL_HEADER': ('HTTP_X_FORWARDED_PROTO', 'https') if production else None,
        'USE_X_FORWARDED_HOST': False,
        'LOGIN_TRUSTED_PROXY_IPS': tuple(h.strip() for h in env.get('DJANGO_TRUSTED_PROXY_IPS', '127.0.0.1,::1' if production else '').split(',') if h.strip()),
    }
