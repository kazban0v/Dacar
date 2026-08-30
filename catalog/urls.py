from django.urls import path
from catalog import views

urlpatterns = [
    path('', views.product_list_view, name='product_list'),
    path('add/', views.product_create_view, name='product_create'),
    path('<int:pk>/edit/', views.product_edit_view, name='product_edit'),
    path('stock/movements/', views.stock_movement_view, name='stock_movement'),
    path('api/search/', views.ProductSearchAPIView.as_view(), name='api_product_search'),
    path('api/categories/create/', views.create_category_api, name='api_create_category'),
]
