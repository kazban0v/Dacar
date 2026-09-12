"""
Django settings for DACAR Detailing Market POS & Inventory Accounting System.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is installed in normal environments
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent

if load_dotenv:
    load_dotenv(BASE_DIR / '.env')

from config.security_settings import security_settings

globals().update(security_settings(os.environ))

# Application definition

INSTALLED_APPS = [
    'config.apps.DacarAdminConfig',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third-party apps
    'rest_framework',
    
    # Local apps
    'users',
    'catalog',
    'sales',
    'analytics',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'users.login_throttle.LoginThrottleMiddleware',
    'config.middleware.MobileLoginRedirectMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'users.middleware.RegistrationControlMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'config.context_processors.dacar_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Custom User Model
AUTH_USER_MODEL = 'users.User'

# Registration Control Flag
ALLOW_REGISTRATION = False

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 4}},
]

# Internationalization
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Asia/Almaty'
USE_I18N = True
USE_TZ = True

# Static files & Media
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Authentication URLs
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'pos'
LOGOUT_REDIRECT_URL = 'login'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# POS Thermal Printer Configuration
THERMAL_PRINTER_NAME = os.environ.get('THERMAL_PRINTER_NAME') or None
# EPT371U's installed TSPL2 driver uses normal browser/OS printing.
# Opt in only after confirming that the selected device accepts ESC/POS.
THERMAL_PRINT_TRANSPORT = os.environ.get('THERMAL_PRINT_TRANSPORT', 'browser')
SHOP_NAME = 'DACAR ДЕТЕЙЛИНГ МАРКЕТ'
SHOP_TAGLINE = ''
SHOP_ADDRESS = 'г. Актобе, ул. Алтын Орда 19д'
SHOP_PHONE = '+7 (706) 806-66-36'

# Login counters are shared by workers, not stored in process-local cache.
LOGIN_THROTTLE_DB_PATH = BASE_DIR / 'runtime' / 'security' / 'login.sqlite3'
LOGIN_THROTTLE_PAIR_LIMIT = 5
LOGIN_THROTTLE_IP_LIMIT = 60
LOGIN_THROTTLE_WINDOW = 300

# Private, bounded monitoring history, separate from business data.
MONITOR_DB_PATH = BASE_DIR / 'runtime' / 'monitor' / 'metrics.sqlite3'
MONITOR_LABEL = os.environ.get('MONITOR_LABEL', '')

# Optional Telegram delivery for monitor alert transitions. Keep credentials in .env only.
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
TELEGRAM_WEBHOOK_SECRET = os.environ.get('TELEGRAM_WEBHOOK_SECRET', '').strip()
TELEGRAM_ALERTS_ENABLED = os.environ.get('TELEGRAM_ALERTS_ENABLED', '').strip().lower() in {
    '1', 'true', 'yes', 'on'
}
