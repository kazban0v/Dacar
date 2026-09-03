"""
DACAR Rendering Module — Desktop / Mobile Template Dispatcher

Priority order for is_mobile_request():
1. URL path: /m/... → always mobile (even if opened in desktop Chrome)
2. User-Agent: contains 'DACARMobileApp' or 'DACAR_Android_Native_POS' → mobile
3. Query param: ?mobile=1 → mobile (dev/debug convenience)
4. Otherwise → desktop

This module provides a drop-in replacement for django.shortcuts.render()
that automatically selects the correct template set (desktop/ or mobile/).
"""

from django.shortcuts import render as django_render


def is_mobile_request(request):
    """
    Determine whether the request comes from the DACAR mobile app.

    Priority: path > User-Agent > query param.
    If /m/ is in the path, always return True — even if the User-Agent
    is a regular desktop browser (someone opened /m/ manually in Chrome).
    """
    # 1. URL path prefix — highest priority, always wins
    if request.path.startswith('/m/'):
        return True

    # 2. User-Agent header
    ua = request.META.get('HTTP_USER_AGENT', '')
    if 'DACARMobileApp' in ua or 'DACAR_Android_Native_POS' in ua:
        return True

    # 3. Query parameter (dev/debug convenience)
    if request.GET.get('mobile') == '1':
        return True

    return False


# Templates that exist ONLY in desktop (no mobile counterpart)
_DESKTOP_ONLY_TEMPLATES = {
    'sales/pos_terminal.html',
    'sales/print_receipt.html',
    'sales/refund_confirm.html',
    'catalog/stock_movement.html',
    'analytics/audit_log.html',
}


def render(request, template_name, context=None, content_type=None,
           status=None, using=None):
    """
    Drop-in replacement for django.shortcuts.render().

    Automatically prefixes the template path with 'desktop/' or 'mobile/'
    based on is_mobile_request(). Desktop-only templates (POS, print receipt,
    refund, audit log) always resolve to desktop/ regardless of the request.
    """
    if context is None:
        context = {}

    mobile = is_mobile_request(request)

    # Inject platform flag into context for template logic
    context['is_mobile'] = mobile

    # Desktop-only templates — always resolve to desktop/
    if template_name in _DESKTOP_ONLY_TEMPLATES:
        resolved = f'desktop/{template_name}'
    elif mobile:
        resolved = f'mobile/{template_name}'
    else:
        resolved = f'desktop/{template_name}'

    return django_render(request, resolved, context,
                         content_type=content_type, status=status,
                         using=using)
