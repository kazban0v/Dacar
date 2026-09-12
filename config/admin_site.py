import logging
import sqlite3

from django.contrib.admin import AdminSite
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.template.response import TemplateResponse
from django.urls import path
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


class DacarAdminSite(AdminSite):
    # Preserve model registrations and Django's individual model permissions.
    index_template = 'admin/dacar_index.html'

    def has_permission(self, request):
        return super().has_permission(request) and request.user.is_admin_user

    def get_urls(self):
        return [
            path('monitor/', self.admin_view(require_GET(self.monitor)), name='server_monitor'),
            path('monitor/data/', self.admin_view(require_GET(self.monitor_data)), name='server_monitor_data'),
        ] + super().get_urls()

    def monitor(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied
        return TemplateResponse(request, 'admin/server_monitor.html', {
            **self.each_context(request), 'title': 'Состояние сервера',
        })

    def monitor_data(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied
        from config.monitoring import get_metrics
        try:
            payload = get_metrics(history=request.GET.get('history') == '1')
        except (OSError, sqlite3.Error, ImportError, ValueError):
            logger.exception('Server monitor unavailable')
            return JsonResponse({'error': 'Метрики временно недоступны. Проверьте зависимости и права на хранилище мониторинга.'}, status=503)
        return JsonResponse(payload)
