import os

from django.conf import settings


def _application_version():
    """Return the release version from the environment or the VERSION file."""
    environment_version = os.environ.get('DACAR_APP_VERSION', '').strip()
    if environment_version:
        return environment_version

    version_file = settings.BASE_DIR / 'VERSION'
    try:
        version = version_file.read_text(encoding='utf-8').strip()
    except OSError:
        version = ''
    return version or '1.0'

def dacar_context(request):
    """
    Context processor injecting shop configuration and registration status to all templates.
    """
    return {
        'SHOP_NAME': getattr(settings, 'SHOP_NAME', 'DACAR Детейлинг Маркет'),
        'SHOP_TAGLINE': getattr(settings, 'SHOP_TAGLINE', ''),
        'SHOP_PHONE': getattr(settings, 'SHOP_PHONE', '+7 (705) 537-11-69'),
        'SHOP_ADDRESS': getattr(settings, 'SHOP_ADDRESS', 'г. Актобе, ул. Алтын Орда 19д'),
        'ALLOW_REGISTRATION': getattr(settings, 'ALLOW_REGISTRATION', True),
        'APP_VERSION': _application_version(),
    }
