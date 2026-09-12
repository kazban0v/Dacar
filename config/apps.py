from django.contrib.admin.apps import AdminConfig


class DacarAdminConfig(AdminConfig):
    default_site = 'config.admin_site.DacarAdminSite'
