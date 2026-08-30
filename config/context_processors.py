from django.conf import settings

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
    }
