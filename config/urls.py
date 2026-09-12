import os
from django.contrib import admin
from django.urls import path, include
from django.shortcuts import render as django_render
from django.http import HttpResponse, FileResponse
from django.conf import settings
from analytics import views as analytics_views
from config.telegram_monitor import telegram_monitor_webhook

def serve_manifest(request):
    manifest_path = os.path.join(settings.BASE_DIR, 'static', 'manifest.json')
    return FileResponse(open(manifest_path, 'rb'), content_type='application/manifest+json')

def serve_sw(request):
    sw_path = os.path.join(settings.BASE_DIR, 'static', 'sw.js')
    return FileResponse(open(sw_path, 'rb'), content_type='application/javascript')

urlpatterns = [
    # === Admin ===
    path('admin/', admin.site.urls),

    # === Desktop routes (original, no prefix) ===
    path('', analytics_views.dashboard_view, name='dashboard'),
    path('audit-log/', analytics_views.audit_log_view, name='audit_log'),
    path('api/analytics/data/', analytics_views.analytics_chart_data_api, name='api_analytics_data'),
    path('analytics/export.csv', analytics_views.analytics_export_csv, name='analytics_export_csv'),
    path('api/analytics/live-kpi/', analytics_views.live_kpi_api, name='api_live_kpi'),
    path('api/analytics/ai-insight/', analytics_views.ai_insight_api, name='api_ai_insight'),
    path('api/notifications/', analytics_views.notifications_api, name='notifications_api'),
    path('api/notifications/action/', analytics_views.notifications_action_api, name='notifications_action_api'),
    path('api/monitor/telegram/<str:secret>/', telegram_monitor_webhook, name='telegram_monitor_webhook'),
    path('manifest.json', serve_manifest, name='pwa_manifest'),
    path('sw.js', serve_sw, name='pwa_sw'),
    path('preview-404/', lambda r: django_render(r, 'desktop/404.html'), name='preview_404'),
    path('preview-500/', lambda r: django_render(r, 'desktop/500.html'), name='preview_500'),
    path('users/', include('users.urls')),
    path('catalog/', include('catalog.urls')),
    path('sales/', include('sales.urls')),

    # === Mobile routes (prefix /m/, same views, different templates via rendering.py) ===
    # The views use config.rendering.render() which detects /m/ prefix and
    # automatically selects templates/mobile/ instead of templates/desktop/.
    path('m/', analytics_views.dashboard_view, name='m_dashboard'),
    path('m/audit-log/', analytics_views.audit_log_view, name='m_audit_log'),
    path('m/api/analytics/data/', analytics_views.analytics_chart_data_api, name='m_api_analytics_data'),
    path('m/analytics/export.csv', analytics_views.analytics_export_csv, name='m_analytics_export_csv'),
    path('m/api/analytics/live-kpi/', analytics_views.live_kpi_api, name='m_api_live_kpi'),
    path('m/api/analytics/ai-insight/', analytics_views.ai_insight_api, name='m_api_ai_insight'),
    path('m/users/', include('users.urls_mobile')),
    path('m/catalog/', include('catalog.urls_mobile')),
    path('m/sales/', include('sales.urls_mobile')),
]
