from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from catalog.models import Category, Brand, Product, StockMovement
from sales.models import SaleOrder, SaleOrderItem
from decimal import Decimal
import random

User = get_user_model()

class Command(BaseCommand):
    help = 'Seeds initial DACAR Detailing Market staff users and auto-chemistry products.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("=== Starting DACAR Data Seeding ==="))

        # 1. Seed Main Admin User
        users_data = [
            {'username': 'admin', 'first_name': 'Админ', 'last_name': 'DACAR', 'role': User.Role.ADMIN, 'pass': 'admin123', 'is_superuser': True, 'is_staff': True},
        ]

        created_users = []
        for udata in users_data:
            user, created = User.objects.get_or_create(
                username=udata['username'],
                defaults={
                    'first_name': udata['first_name'],
                    'last_name': udata['last_name'],
                    'role': udata['role'],
                    'is_superuser': udata['is_superuser'],
                    'is_staff': udata['is_staff'],
                }
            )
            if created or not user.check_password(udata['pass']):
                user.set_password(udata['pass'])
                user.save()
            created_users.append(user)
            self.stdout.write(f"User check/created: {user.username} ({user.get_role_display()})")

        # 2. Seed Categories
        categories_data = [
            {'name': 'Автошампуни и Пена', 'slug': 'shampoos', 'icon': 'fa-soap', 'description': 'Шампуни для бесконтактной и ручной мойки'},
            {'name': 'Очистители дисков и шин', 'slug': 'wheels-tires', 'icon': 'fa-circle-notch', 'description': 'Очистители тормозной пыли, чернители шин'},
            {'name': 'Очистка салона и кожи', 'slug': 'interior', 'icon': 'fa-couch', 'description': 'Кондиционеры кожи, химчистка салона, пластик'},
            {'name': 'Полировка и Воски', 'slug': 'polishing', 'icon': 'fa-sparkles', 'description': 'Полировальные пасты, твердые воски, силанты'},
            {'name': 'Керамика и Защитные покрытия', 'slug': 'ceramics', 'icon': 'fa-shield-halved', 'description': 'Жидкое стекло, керамические составы 9H, антидождь'},
            {'name': 'Микрофибры и Аксессуары', 'slug': 'microfibers', 'icon': 'fa-toilet-paper', 'description': 'Сушильные полотенца, микрофибры, аппликаторы, кисти'},
        ]

        cat_objs = {}
        for cdata in categories_data:
            cat, _ = Category.objects.get_or_create(slug=cdata['slug'], defaults=cdata)
            cat_objs[cdata['slug']] = cat

        # 3. Seed Brands
        brands_data = [
            {'name': 'Koch Chemie', 'slug': 'koch-chemie', 'country': 'Германия'},
            {'name': 'Detail (Grass)', 'slug': 'detail', 'country': 'Россия'},
            {'name': 'Meguiar\'s', 'slug': 'meguiars', 'country': 'США'},
            {'name': 'Shine Systems', 'slug': 'shine-systems', 'country': 'Россия'},
            {'name': 'Soft99', 'slug': 'soft99', 'country': 'Япония'},
            {'name': 'Gyeon', 'slug': 'gyeon', 'country': 'Южная Корея'},
        ]

        brand_objs = {}
        for bdata in brands_data:
            brand, _ = Brand.objects.get_or_create(slug=bdata['slug'], defaults=bdata)
            brand_objs[bdata['slug']] = brand

        # 4. Seed Products
        products_list = [
            {
                'name': 'Автошампунь Detail Nano Shampoo (1 л)',
                'sku': 'DAC-101',
                'barcode': '4607001234501',
                'category': cat_objs['shampoos'],
                'brand': brand_objs['detail'],
                'purchase_price': Decimal('2800.00'),
                'retail_price': Decimal('4500.00'),
                'unit': 'л',
                'stock_qty': Decimal('35.000'),
                'min_stock_alert': Decimal('5.000'),
            },
            {
                'name': 'Пена для бесконтактной мойки Koch Chemie Gentle Snow Foam (1 л)',
                'sku': 'DAC-102',
                'barcode': '4607001234502',
                'category': cat_objs['shampoos'],
                'brand': brand_objs['koch-chemie'],
                'purchase_price': Decimal('5500.00'),
                'retail_price': Decimal('8900.00'),
                'unit': 'л',
                'stock_qty': Decimal('18.000'),
                'min_stock_alert': Decimal('4.000'),
            },
            {
                'name': 'Очиститель дисков Shine Systems IronOff (500 мл)',
                'sku': 'DAC-103',
                'barcode': '4607001234503',
                'category': cat_objs['wheels-tires'],
                'brand': brand_objs['shine-systems'],
                'purchase_price': Decimal('2200.00'),
                'retail_price': Decimal('3800.00'),
                'unit': 'шт',
                'stock_qty': Decimal('42.000'),
                'min_stock_alert': Decimal('8.000'),
            },
            {
                'name': 'Кондиционер и очиститель кожи Meguiar\'s Ultimate Leather Detailer',
                'sku': 'DAC-104',
                'barcode': '4607001234504',
                'category': cat_objs['interior'],
                'brand': brand_objs['meguiars'],
                'purchase_price': Decimal('6200.00'),
                'retail_price': Decimal('9800.00'),
                'unit': 'шт',
                'stock_qty': Decimal('12.000'),
                'min_stock_alert': Decimal('3.000'),
            },
            {
                'name': 'Защитное покрытие Керамика Gyeon Q2 Flash (50 мл)',
                'sku': 'DAC-105',
                'barcode': '4607001234505',
                'category': cat_objs['ceramics'],
                'brand': brand_objs['gyeon'],
                'purchase_price': Decimal('24000.00'),
                'retail_price': Decimal('38500.00'),
                'unit': 'шт',
                'stock_qty': Decimal('6.000'),
                'min_stock_alert': Decimal('2.000'),
            },
            {
                'name': 'Твердый воск Soft99 Fussu Coat Black (200 г)',
                'sku': 'DAC-106',
                'barcode': '4607001234506',
                'category': cat_objs['polishing'],
                'brand': brand_objs['soft99'],
                'purchase_price': Decimal('7800.00'),
                'retail_price': Decimal('12500.00'),
                'unit': 'шт',
                'stock_qty': Decimal('15.000'),
                'min_stock_alert': Decimal('3.000'),
            },
            {
                'name': 'Микрофибра сушильная DACAR Extra Dry 60x90 (800 GSM)',
                'sku': 'DAC-107',
                'barcode': '4607001234507',
                'category': cat_objs['microfibers'],
                'brand': brand_objs['shine-systems'],
                'purchase_price': Decimal('2500.00'),
                'retail_price': Decimal('4900.00'),
                'unit': 'шт',
                'stock_qty': Decimal('50.000'),
                'min_stock_alert': Decimal('10.000'),
            },
            {
                'name': 'Набор кистей для деталей интерьера (5 шт)',
                'sku': 'DAC-108',
                'barcode': '4607001234508',
                'category': cat_objs['microfibers'],
                'brand': brand_objs['detail'],
                'purchase_price': Decimal('1800.00'),
                'retail_price': Decimal('3200.00'),
                'unit': 'компл',
                'stock_qty': Decimal('25.000'),
                'min_stock_alert': Decimal('5.000'),
            },
            {
                'name': 'Антидождь Soft99 Glaco Roll On Large (120 мл)',
                'sku': 'DAC-109',
                'barcode': '4607001234509',
                'category': cat_objs['ceramics'],
                'brand': brand_objs['soft99'],
                'purchase_price': Decimal('3500.00'),
                'retail_price': Decimal('5900.00'),
                'unit': 'шт',
                'stock_qty': Decimal('20.000'),
                'min_stock_alert': Decimal('4.000'),
            },
            {
                'name': 'Чернитель шин Koch Chemie Gummifix (1 л)',
                'sku': 'DAC-110',
                'barcode': '4607001234510',
                'category': cat_objs['wheels-tires'],
                'brand': brand_objs['koch-chemie'],
                'purchase_price': Decimal('4200.00'),
                'retail_price': Decimal('7200.00'),
                'unit': 'л',
                'stock_qty': Decimal('14.000'),
                'min_stock_alert': Decimal('3.000'),
            },
        ]

        for pdata in products_list:
            prod, pcreated = Product.objects.get_or_create(
                sku=pdata['sku'],
                defaults=pdata
            )
            if pcreated:
                # Log initial stock movement
                StockMovement.objects.create(
                    product=prod,
                    movement_type=StockMovement.MovementType.IN,
                    quantity=prod.stock_qty,
                    cost_price=prod.purchase_price,
                    comment="Начальный остаток при старте системы",
                    created_by=created_users[0]
                )
                self.stdout.write(f"Product created: {prod.name}")

        # 5. Create a sample completed sales order
        if not SaleOrder.objects.exists():
            order_num = SaleOrder.generate_order_number()
            cashier = created_users[2] # cashier1
            p1 = Product.objects.get(sku='DAC-101')
            p2 = Product.objects.get(sku='DAC-107')
            
            subtotal = (p1.retail_price * Decimal('2')) + (p2.retail_price * Decimal('1'))
            discount = Decimal('400.00')
            total = subtotal - discount
            paid = Decimal('15000.00')
            change = paid - total

            order = SaleOrder.objects.create(
                order_number=order_num,
                cashier=cashier,
                status=SaleOrder.Status.COMPLETED,
                payment_method=SaleOrder.PaymentMethod.CARD,
                subtotal_amount=subtotal,
                discount_amount=discount,
                total_amount=total,
                paid_amount=paid,
                change_amount=change,
                notes="Тестовая стартовая продажа"
            )

            SaleOrderItem.objects.create(
                order=order,
                product=p1,
                quantity=Decimal('2'),
                purchase_price_snapshot=p1.purchase_price,
                unit_price=p1.retail_price,
                discount_amount=Decimal('300.00'),
                total_amount=(p1.retail_price * Decimal('2')) - Decimal('300.00')
            )

            SaleOrderItem.objects.create(
                order=order,
                product=p2,
                quantity=Decimal('1'),
                purchase_price_snapshot=p2.purchase_price,
                unit_price=p2.retail_price,
                discount_amount=Decimal('100.00'),
                total_amount=p2.retail_price - Decimal('100.00')
            )
            self.stdout.write(self.style.SUCCESS(f"Sample sale order {order.order_number} created!"))

        self.stdout.write(self.style.SUCCESS("=== DACAR Data Seeding Completed Successfully! ==="))
