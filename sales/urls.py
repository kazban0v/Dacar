from django.urls import path
from sales import views
from sales import document_views

urlpatterns = [
    path('reports/shine/', document_views.shine_report, name='shine_report'),
    path('invoices/', document_views.invoices, name='company_invoices'),
    path('invoices/<int:pk>/pdf/', document_views.invoice_download, name='invoice_pdf'),
    path('api/invoices/', document_views.InvoiceCreateAPIView.as_view(), name='invoice_create'),
    path('pos/', views.pos_interface_view, name='pos'),
    path('orders/', views.sales_orders_list_view, name='sales_orders_list'),
    path('orders/<int:pk>/print/', views.order_detail_print_view, name='order_print'),
    path('orders/<int:pk>/refund/', views.order_refund_view, name='order_refund'),
    path('orders/<int:pk>/delete/', views.order_delete_view, name='order_delete'),
    path('api/checkout/', views.CheckoutAPIView.as_view(), name='api_checkout'),
    path('api/orders/<int:pk>/print-raw/', views.OrderDirectPrintAPIView.as_view(), name='api_order_print_raw'),
]
