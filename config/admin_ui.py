from decimal import Decimal

from django.utils.html import format_html

from catalog.templatetags.dacar_format import format_tenge


class DacarAdminMixin:
    """Shared, compact admin presentation without changing model behaviour."""

    admin_description = ''
    list_per_page = 50
    list_max_show_all = 200
    show_full_result_count = False
    save_on_top = False

    def _page_context(self, extra_context=None):
        return {
            'admin_description': self.admin_description,
            **(extra_context or {}),
        }

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, self._page_context(extra_context))

    def add_view(self, request, form_url='', extra_context=None):
        return super().add_view(request, form_url, self._page_context(extra_context))

    def change_view(self, request, object_id, form_url='', extra_context=None):
        return super().change_view(
            request, object_id, form_url, self._page_context(extra_context)
        )


def money(value):
    if value is None:
        return '—'
    return f'{format_tenge(Decimal(value))} ₸'


def quantity(value, unit='шт'):
    if value is None:
        return '—'
    value = Decimal(value)
    rendered = f'{value:f}'.rstrip('0').rstrip('.') if value % 1 else str(int(value))
    return f'{rendered} {unit}'


def status_badge(label, tone='neutral'):
    return format_html('<span class="dc-status dc-status-{}">{}</span>', tone, label)
