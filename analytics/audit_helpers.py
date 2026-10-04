"""Structured audit helper: builds «was → became» diffs for AuditLog.metadata."""
import re
from decimal import Decimal

from catalog.templatetags.dacar_format import format_tenge


# Human-readable field labels shared across the project.
FIELD_LABELS = {
    # Product
    'name': 'Название',
    'sku': 'Артикул',
    'barcode': 'Штрихкод',
    'category': 'Категория',
    'brand': 'Бренд',
    'purchase_price': 'Закупочная цена',
    'retail_price': 'Розничная цена',
    'unit': 'Единица измерения',
    'min_stock_alert': 'Мин. остаток',
    'stock_qty': 'Остаток',
    'is_active': 'Активен',
    'comment': 'Комментарий',
    # User
    'role': 'Роль',
    'is_active_user': 'Статус аккаунта',
    'first_name': 'Имя',
    'last_name': 'Фамилия',
    'phone': 'Телефон',
    'username': 'Логин',
}


def build_change_diff(old_values, new_values, *, labels=None):
    """Compare two flat dicts and return a structured diff.

    Returns::

        {
            "changes": [
                {"field": "retail_price", "label": "Розничная цена", "old": "3500", "new": "4200"},
                ...
            ]
        }

    Only fields present in *both* dicts and whose values actually changed are
    included.  Empty changes list means nothing changed.
    """
    labels = {**FIELD_LABELS, **(labels or {})}
    changes = []
    for key in sorted(set(old_values) & set(new_values)):
        old_val = _normalize(old_values[key])
        new_val = _normalize(new_values[key])
        if old_val != new_val:
            changes.append({
                'field': key,
                'label': labels.get(key, key),
                'old': _display(old_values[key]),
                'new': _display(new_values[key]),
            })
    return {'changes': changes}


def diff_description(diff: dict) -> str:
    """Build a human-readable single-line description from a diff dict."""
    parts = []
    for change in diff.get('changes', []):
        parts.append(f'{change["label"]}: «{change["old"]}» → «{change["new"]}»')
    return '; '.join(parts) or 'Без изменений'


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize(value):
    """Normalize a value for comparison."""
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, float):
        return Decimal(str(value)).normalize()
    if value is None:
        return ''
    return str(value).strip()


def _display(value):
    """Format a value for human-readable display in the diff."""
    if value is None or value == '':
        return '—'
    if isinstance(value, bool):
        return 'Да' if value else 'Нет'
    if isinstance(value, Decimal):
        return format_tenge(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return format_tenge(value)
    return str(value)


def display_audit_description(value):
    """Keep historic text immutable while removing meaningless quantity zeros.

    Older audit rows were written directly from Decimal values, so a stock
    movement could read ``5.000 шт``.  The database text remains untouched;
    only its on-screen representation is normalised.
    """
    def trim(match):
        number = Decimal(match.group('number'))
        if number == number.to_integral():
            return f'{number.quantize(Decimal("1"))}{match.group("suffix")}'
        return f'{number.normalize():f}{match.group("suffix")}'

    return re.sub(
        r'(?P<number>-?\d+\.\d+)(?P<suffix>\s*(?:шт|л|мл|кг|компл)\b|(?=[,.)]))',
        trim,
        str(value or ''),
    )
