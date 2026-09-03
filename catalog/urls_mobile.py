from django.urls import path
from catalog import views

# Mobile URL patterns — same views as desktop, different names to avoid clashes
urlpatterns = [
    path('', views.product_list_view, name='m_product_list'),
    path('add/', views.product_create_view, name='m_product_create'),
    path('<int:pk>/edit/', views.product_edit_view, name='m_product_edit'),
    path('stock/movements/', views.stock_movement_view, name='m_stock_movement'),
    path('api/search/', views.ProductSearchAPIView.as_view(), name='m_api_product_search'),
    path('api/categories/create/', views.create_category_api, name='m_api_create_category'),
    path('api/stock-action/', views.stock_action_api, name='m_api_stock_action'),
]
