from django.contrib import admin
from django.urls import path, include
from analytics import views as analytics_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', analytics_views.dashboard_view, name='dashboard'),
    path('audit-log/', analytics_views.audit_log_view, name='audit_log'),
    path('api/analytics/data/', analytics_views.analytics_chart_data_api, name='api_analytics_data'),
    path('users/', include('users.urls')),
    path('catalog/', include('catalog.urls')),
    path('sales/', include('sales.urls')),
]
