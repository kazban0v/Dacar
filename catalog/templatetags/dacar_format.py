from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


CATEGORY_ICONS = {
    'автошампунь': 'fa-soap',
    'гидрофоб/кварц': 'fa-gem',
    'детейлинг спрей': 'fa-spray-can-sparkles',
    'диски': 'fa-compact-disc',
    'керамика': 'fa-gem',
    'покрышка': 'fa-circle-dot',
    'полироль': 'fa-wand-magic-sparkles',
    'салон': 'fa-car-side',
    'стекло': 'fa-window-maximize',
    'тряпки': 'fa-hand-sparkles',
    'тряпка': 'fa-hand-sparkles',
    'микрофибра': 'fa-hand-sparkles',
    'микрофибры': 'fa-hand-sparkles',
    'полотенца': 'fa-hand-sparkles',
    'салфетки': 'fa-hand-sparkles',
    'химия': 'fa-flask',
    'чернитель': 'fa-fill-drip',
    'шампунь микрофибра': 'fa-soap',
    'интерьер': 'fa-car-side',
    'экстерьер': 'fa-car',
    'автохимия и аксессуары': 'fa-spray-can-sparkles',
}


def format_tenge(value):
    """Format amounts as 34 000 / 1 250,25 for UI, audit, and reports."""
    try:
        amount = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return value

    if amount == amount.to_integral():
        return f'{amount.quantize(Decimal("1")):,}'.replace(',', ' ')
    return f'{amount:,.2f}'.replace(',', ' ').replace('.', ',')


@register.filter
def tenge(value):
    """Format whole-tenge amounts consistently across reports and mobile pages."""
    return format_tenge(value)


@register.filter
def quantity(value):
    """Show stock quantities without meaningless trailing zeroes."""
    try:
        amount = Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return value
    if amount == amount.to_integral():
        return str(int(amount))
    rendered = f'{amount.normalize():f}'.rstrip('0').rstrip('.')
    return rendered.replace('.', ',') if rendered else '0'


@register.filter
def category_icon(name, fallback='fa-tag'):
    """Use a meaningful icon even for legacy categories stored with fa-tag."""
    normalized = str(name or '').strip().lower()
    return CATEGORY_ICONS.get(normalized, fallback or 'fa-tag')
