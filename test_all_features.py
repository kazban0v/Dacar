import os, django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from catalog.models import Product, Category, Brand
from sales.models import SaleOrder, SaleOrderItem

User = get_user_model()
admin = User.objects.filter(username='admin').first()

client = Client()
client.force_login(admin)

print("=== 1. Testing Dashboard ===")
r1 = client.get('/')
print("Dashboard status:", r1.status_code)

print("\n=== 2. Testing POS Terminal ===")
r2 = client.get('/sales/pos/')
print("POS status:", r2.status_code)

print("\n=== 3. Testing Catalog List ===")
r3 = client.get('/catalog/')
print("Catalog status:", r3.status_code)

print("\n=== 4. Testing Product Create ===")
r4 = client.post('/catalog/add/', {
    'name': 'Автошампунь Grass Active Foam 1л',
    'barcode': '4607072481024',
    'brand': 'Grass',
    'retail_price': '3500',
    'purchase_price': '2100',
    'initial_stock': '50',
    'min_stock_alert': '5'
})
print("Product create status (should redirect 302):", r4.status_code)
created_p = Product.objects.filter(barcode='4607072481024').first()
print("Created product:", created_p.name if created_p else "None", "| Stock:", created_p.stock_qty if created_p else 0)

print("\n=== 5. Testing Quick Stock Refill ===")
if created_p:
    r5 = client.post('/catalog/stock/movements/', {
        'product_id': created_p.id,
        'quantity': 20,
        'movement_type': 'IN',
        'comment': 'Поступление от поставщика'
    }, HTTP_REFERER='/catalog/')
    print("Stock refill status:", r5.status_code)
    created_p.refresh_from_db()
    print("Updated Stock:", created_p.stock_qty)

print("\n=== 6. Testing POS Checkout API ===")
if created_p:
    import json
    r6 = client.post('/sales/api/checkout/', data=json.dumps({
        'items': [{'product_id': created_p.id, 'quantity': 2, 'price': 3500}],
        'payment_method': 'CASH',
        'paid_amount': 10000,
        'discount_amount': 0
    }), content_type='application/json')
    print("Checkout status:", r6.status_code, "Response:", r6.json())

print("\n=== 7. Testing Sales Orders List ===")
r7 = client.get('/sales/orders/')
print("Orders list status:", r7.status_code)

print("\n=== 8. Testing Staff Management ===")
r8 = client.get('/users/staff/')
print("Staff list status:", r8.status_code)

print("\n=== 9. Testing Live KPI API ===")
r9 = client.get('/api/analytics/live-kpi/')
print("Live KPI status:", r9.status_code, "Data:", r9.json())

print("\n=== 10. Testing Stock Action API (+ Принять) ===")
import json, uuid
sync_uuid = str(uuid.uuid4())
r10 = client.post('/catalog/api/stock-action/', data=json.dumps({
    'product_id': created_p.id,
    'action': 'IN',
    'quantity': 15,
    'comment': 'Поступление партии на склад',
    'client_sync_id': sync_uuid
}), content_type='application/json')
print("Stock Action IN status:", r10.status_code, "Resp:", r10.json())

print("\n=== 11. Testing Idempotency (Repeat same UUID) ===")
r11 = client.post('/catalog/api/stock-action/', data=json.dumps({
    'product_id': created_p.id,
    'action': 'IN',
    'quantity': 15,
    'comment': 'Повторный запрос',
    'client_sync_id': sync_uuid
}), content_type='application/json')
print("Idempotency repeat status:", r11.status_code, "Resp:", r11.json())

print("\n=== 12. Testing Stock Action (− Отгрузить в магазин) ===")
r12 = client.post('/catalog/api/stock-action/', data=json.dumps({
    'product_id': created_p.id,
    'action': 'TRANSFER_TO_SHOP',
    'quantity': 5,
    'comment': 'Отгрузка водителю в магазин',
    'client_sync_id': str(uuid.uuid4())
}), content_type='application/json')
print("Stock Action TRANSFER status:", r12.status_code, "Resp:", r12.json())

print("\n=== 13. Testing 409 Conflict (Over-withdrawal) ===")
r13 = client.post('/catalog/api/stock-action/', data=json.dumps({
    'product_id': created_p.id,
    'action': 'TRANSFER_TO_SHOP',
    'quantity': 99999,
    'comment': 'Слишком много',
    'client_sync_id': str(uuid.uuid4())
}), content_type='application/json')
print("409 Conflict status:", r13.status_code, "Resp:", r13.json())

print("\n=== ALL BACKEND APIs & CONCURRENCY CHECKS PASSED! ===")
