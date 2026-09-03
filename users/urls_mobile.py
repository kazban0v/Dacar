from django.urls import path
from users import views

# Mobile URL patterns — same views as desktop, different names to avoid clashes
urlpatterns = [
    path('login/', views.login_view, name='m_login'),
    path('logout/', views.logout_view, name='m_logout'),
    path('register/', views.register_view, name='m_register'),
    path('staff/', views.users_list_view, name='m_users_list'),
]
