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
        'SHOP_NAME': 'DACAR Детейлинг Маркет',
        'SHOP_TAGLINE': '',
        'SHOP_PHONE': '+7 (706) 806-66-36',
        'SHOP_ADDRESS': 'г. Актобе, ул. Алтын Орда 19д',
        'ALLOW_REGISTRATION': getattr(settings, 'ALLOW_REGISTRATION', True),
        'APP_VERSION': _application_version(),
    }
