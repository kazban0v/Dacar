from django.shortcuts import redirect, get_object_or_404
from config.rendering import render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, F
from django.http import JsonResponse
from catalog.models import Product, Category, Brand, StockMovement
from analytics.models import AuditLog
from catalog.serializers import ProductSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from decimal import Decimal
import random

@login_required
def product_list_view(request):
    search = request.GET.get('q', '').strip()
    category_id = request.GET.get('category')
    brand_id = request.GET.get('brand')
    low_stock = request.GET.get('low_stock')

    products = Product.objects.select_related('category', 'brand').filter(is_active=True)

    if search:
        products = products.filter(
            Q(name__icontains=search) |
            Q(sku__icontains=search) |
            Q(barcode__icontains=search)
        )
    if category_id:
        products = products.filter(category_id=category_id)
    if brand_id:
        products = products.filter(brand_id=brand_id)
    if low_stock == '1':
        products = products.filter(stock_qty__lte=F('min_stock_alert'))

    from django.core.paginator import Paginator

    categories = Category.objects.all()
    brands = Brand.objects.all()

    page_number = request.GET.get('page', 1)
    paginator = Paginator(products, 15)
    page_obj = paginator.get_page(page_number)

    return render(request, 'catalog/product_list.html', {
        'page_obj': page_obj,
        'products': page_obj.object_list,
        'categories': categories,
        'brands': brands,
        'search': search,
        'selected_category': category_id,
        'selected_brand': brand_id,
        'low_stock_filter': low_stock,
    })


@login_required
def product_create_view(request):
    if not request.user.is_admin_user:
        messages.error(request, 'Добавление товаров доступно исключительно Администратору.')
        return redirect('product_list')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        sku = request.POST.get('sku', '').strip()
        barcode = request.POST.get('barcode', '').strip()
        category_id = request.POST.get('category')
        brand_input = request.POST.get('brand', '').strip()
        purchase_price = request.POST.get('purchase_price', '').strip() or '0'
        retail_price = request.POST.get('retail_price', '').strip() or '0'
        unit = request.POST.get('unit', 'шт')
        stock_qty = request.POST.get('stock_qty', '').strip() or '0'
        min_stock_alert = request.POST.get('min_stock_alert', '').strip() or '5'

        if not barcode:
            barcode = f"200{random.randint(100000000, 999999999)}"

        if not sku:
            base_code = barcode[-6:] if len(barcode) >= 6 else str(random.randint(100000, 999999))
            sku = f"DAC-{base_code}"
            counter = 1
            while Product.objects.filter(sku=sku).exists():
                sku = f"DAC-{base_code}-{counter}"
                counter += 1

        if Product.objects.filter(barcode=barcode).exists():
            messages.error(request, f'Товар со штрихкодом {barcode} уже существует в базе.')
        elif Product.objects.filter(sku=sku).exists():
            messages.error(request, f'Товар с артикулом {sku} уже существует.')
        else:
            # Handle category (optional fallback to default)
            if category_id:
                category = Category.objects.filter(id=category_id).first()
            else:
                category = None
            if not category:
                category = Category.objects.first() or Category.objects.create(name='Общая', slug='general')

            # Handle brand (manual text input or get_or_create)
            brand = None
            if brand_input:
                brand = Brand.objects.filter(name__iexact=brand_input).first()
                if not brand:
                    from django.utils.text import slugify
                    b_slug = slugify(brand_input) or f"brand-{random.randint(1000, 9999)}"
                    counter = 1
                    orig_slug = b_slug
                    while Brand.objects.filter(slug=b_slug).exists():
                        b_slug = f"{orig_slug}-{counter}"
                        counter += 1
                    brand = Brand.objects.create(name=brand_input, slug=b_slug)

            product = Product.objects.create(
                name=name,
                sku=sku,
                barcode=barcode,
                category=category,
                brand=brand,
                purchase_price=Decimal(purchase_price),
                retail_price=Decimal(retail_price),
                unit=unit,
                stock_qty=Decimal(stock_qty),
                min_stock_alert=Decimal(min_stock_alert)
            )

            # Log stock movement if initial stock > 0
            if Decimal(stock_qty) > Decimal('0'):
                StockMovement.objects.create(
                    product=product,
                    movement_type=StockMovement.MovementType.IN,
                    quantity=Decimal(stock_qty),
                    cost_price=Decimal(purchase_price),
                    comment='Первичный ввод товара на склад',
                    created_by=request.user
                )

            AuditLog.log(
                request,
                AuditLog.ActionType.PRODUCT_CREATE,
                f"Создан новый товар '{product.name}' (Штрихкод: {product.barcode}, Розница: {product.retail_price} ₸)"
            )

            messages.success(request, f'Товар "{product.name}" успешно добавлен.')
            return redirect('product_list')

    categories = Category.objects.all()
    brands = Brand.objects.all()
    initial_barcode = request.GET.get('barcode', '').strip()
    return render(request, 'catalog/product_form.html', {
        'categories': categories,
        'brands': brands,
        'units': Product.UNIT_CHOICES,
        'initial_barcode': initial_barcode,
    })


@login_required
def product_edit_view(request, pk):
    if not request.user.is_admin_user:
        messages.error(request, 'Редактирование товаров доступно исключительно Администратору.')
        return redirect('product_list')

    product = get_object_or_404(Product, pk=pk)

    if request.method == 'POST':
        product.name = request.POST.get('name', '').strip()
        sku_input = request.POST.get('sku', '').strip()
        if sku_input:
            product.sku = sku_input
        product.barcode = request.POST.get('barcode', '').strip()
        category_id = request.POST.get('category')
        brand_input = request.POST.get('brand', '').strip()

        if category_id:
            cat = Category.objects.filter(id=category_id).first()
            if cat:
                product.category = cat
        
        # Handle brand
        if brand_input:
            brand = Brand.objects.filter(name__iexact=brand_input).first()
            if not brand:
                from django.utils.text import slugify
                b_slug = slugify(brand_input) or f"brand-{random.randint(1000, 9999)}"
                counter = 1
                orig_slug = b_slug
                while Brand.objects.filter(slug=b_slug).exists():
                    b_slug = f"{orig_slug}-{counter}"
                    counter += 1
                brand = Brand.objects.create(name=brand_input, slug=b_slug)
            product.brand = brand
        else:
            product.brand = None
        
        product.purchase_price = Decimal(request.POST.get('purchase_price', '0') or '0')
        product.retail_price = Decimal(request.POST.get('retail_price', '0') or '0')
        product.unit = request.POST.get('unit', 'шт')
        product.min_stock_alert = Decimal(request.POST.get('min_stock_alert', '5') or '5')
        product.save()

        AuditLog.log(
            request,
            AuditLog.ActionType.PRODUCT_UPDATE,
            f"Обновлена карточка товара '{product.name}' (Розница: {product.retail_price} ₸)"
        )

        messages.success(request, f'Товар "{product.name}" обновлен.')
        return redirect('product_list')

    categories = Category.objects.all()
    brands = Brand.objects.all()
    return render(request, 'catalog/product_form.html', {
        'product': product,
        'categories': categories,
        'brands': brands,
        'units': Product.UNIT_CHOICES
    })


@login_required
def stock_movement_view(request):
    if not request.user.is_admin_user:
        messages.error(request, 'Складские операции доступны исключительно Администратору.')
        return redirect('pos')

    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        movement_type = request.POST.get('movement_type')
        qty_str = request.POST.get('quantity', '0')
        cost_price_str = request.POST.get('cost_price', '0')
        comment = request.POST.get('comment', '').strip()

        product = get_object_or_404(Product, id=product_id)
        qty = Decimal(qty_str)
        cost_price = Decimal(cost_price_str) if cost_price_str else product.purchase_price

        if movement_type in [StockMovement.MovementType.IN, StockMovement.MovementType.RETURN]:
            product.stock_qty += qty
        elif movement_type in [StockMovement.MovementType.OUT, StockMovement.MovementType.SALE]:
            product.stock_qty = max(Decimal('0.000'), product.stock_qty - qty)
        elif movement_type == StockMovement.MovementType.ADJUSTMENT:
            product.stock_qty = qty
        
        product.save()

        StockMovement.objects.create(
            product=product,
            movement_type=movement_type,
            quantity=qty,
            cost_price=cost_price,
            comment=comment,
            created_by=request.user
        )

        AuditLog.log(
            request,
            AuditLog.ActionType.STOCK_IN if movement_type == 'IN' else AuditLog.ActionType.STOCK_OUT,
            f"Складская операция [{movement_type}] для '{product.name}': {qty} {product.unit}. Комментарий: {comment}"
        )

        messages.success(request, f'Остаток товара "{product.name}" успешно обновлен (+{qty} {product.unit}).')
        referer = request.META.get('HTTP_REFERER')
        if referer and '/catalog/' in referer:
            return redirect(referer)
        return redirect('stock_movement')

    movements = StockMovement.objects.select_related('product', 'created_by').all()[:100]
    products = Product.objects.filter(is_active=True).order_by('name')
    return render(request, 'catalog/stock_movement.html', {
        'movements': movements,
        'products': products,
        'movement_types': StockMovement.MovementType.choices
    })


class ProductSearchAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        query = request.GET.get('q', '').strip()
        barcode = request.GET.get('barcode', '').strip()

        products = Product.objects.select_related('category', 'brand').filter(is_active=True)

        if barcode:
            # Direct exact match for scanner
            product = products.filter(Q(barcode=barcode) | Q(sku=barcode)).first()
            if product:
                serializer = ProductSerializer(product)
                return Response({'found': True, 'product': serializer.data})
            return Response({'found': False, 'message': f'Товар со штрихкодом {barcode} не найден.'})

        if query:
            products = products.filter(
                Q(name__icontains=query) |
                Q(sku__icontains=query) |
                Q(barcode__icontains=query)
            )[:20]
        else:
            products = products[:25]

        serializer = ProductSerializer(products, many=True)
        return Response({'found': True, 'products': serializer.data})


@login_required
def create_category_api(request):
    if not request.user.is_admin_user:
        return JsonResponse({'success': False, 'error': 'Доступ разрешен только администраторам.'}, status=403)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        if not name:
            import json
            try:
                data = json.loads(request.body.decode('utf-8'))
                name = data.get('name', '').strip()
            except Exception:
                pass

        if name:
            from django.utils.text import slugify
            import random
            slug = slugify(name) or f"cat-{random.randint(1000, 9999)}"
            counter = 1
            orig_slug = slug
            while Category.objects.filter(slug=slug).exists():
                slug = f"{orig_slug}-{counter}"
                counter += 1
            category, _ = Category.objects.get_or_create(name=name, defaults={'slug': slug})
            return JsonResponse({'success': True, 'id': category.id, 'name': category.name})

    return JsonResponse({'success': False, 'error': 'Название категории обязательно.'}, status=400)


@login_required
def stock_action_api(request):
    """
    Atomic Stock Operations API for DACAR Mobile Hub (+ Принять / − Отгрузить в магазин).
    Features:
    - @transaction.atomic with row-level select_for_update() (deadlock-free).
    - Idempotency by client_sync_id (UUID v4).
    - 409 Conflict validation when stock is insufficient.
    - Automatic Live KPI cache invalidation.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Метод не поддерживается'}, status=405)

    import json
    from django.db import transaction
    from django.core.cache import cache

    try:
        data = json.loads(request.body.decode('utf-8')) if request.body else request.POST
    except Exception:
        data = request.POST

    product_id = data.get('product_id')
    action = data.get('action', 'IN').upper() # IN, TRANSFER_TO_SHOP, OUT
    quantity_raw = data.get('quantity', 1)
    comment = (data.get('comment') or '').strip()
    client_sync_id = data.get('client_sync_id', '').strip()

    try:
        qty = Decimal(str(quantity_raw))
        if qty <= 0:
            return JsonResponse({'success': False, 'error': 'Количество должно быть больше 0'}, status=400)
    except Exception:
        return JsonResponse({'success': False, 'error': 'Некорректное значение количества'}, status=400)

    # 1. Idempotency Check
    if client_sync_id and StockMovement.objects.filter(client_sync_id=client_sync_id).exists():
        existing_mov = StockMovement.objects.filter(client_sync_id=client_sync_id).first()
        return JsonResponse({
            'success': True,
            'message': 'Операция уже была успешно выполнена (Idempotent)',
            'stock_qty': float(existing_mov.product.stock_qty),
            'product_name': existing_mov.product.name
        })

    with transaction.atomic():
        product = Product.objects.filter(id=product_id).select_for_update().first()
        if not product:
            return JsonResponse({'success': False, 'error': 'Товар не найден'}, status=404)

        prev_stock = product.stock_qty

        # 2. Validation for Outgoing/Transfer Actions (Preventing Negative Stock)
        if action in ['TRANSFER_TO_SHOP', 'OUT', StockMovement.MovementType.TRANSFER_TO_SHOP, StockMovement.MovementType.OUT]:
            if product.stock_qty < qty:
                return JsonResponse({
                    'success': False,
                    'error_code': 'INSUFFICIENT_STOCK',
                    'message': f'Недостаточно товара на складе. Доступно: {product.stock_qty} {product.unit}',
                    'available_stock': float(product.stock_qty),
                    'product_name': product.name
                }, status=409)
            
            product.stock_qty -= qty
            movement_type = StockMovement.MovementType.TRANSFER_TO_SHOP if action == 'TRANSFER_TO_SHOP' else StockMovement.MovementType.OUT
            action_label = "Отгрузка в магазин" if action == 'TRANSFER_TO_SHOP' else "Списание"
        else:
            product.stock_qty += qty
            movement_type = StockMovement.MovementType.IN
            action_label = "Приемка на склад"

        product.save()

        StockMovement.objects.create(
            product=product,
            movement_type=movement_type,
            quantity=qty,
            cost_price=product.purchase_price,
            comment=comment or f"{action_label} через мобильное приложение",
            client_sync_id=client_sync_id or None,
            created_by=request.user
        )

        AuditLog.log(
            request,
            AuditLog.ActionType.STOCK_IN if movement_type == 'IN' else AuditLog.ActionType.STOCK_OUT,
            f"Склад: {action_label} для '{product.name}': {qty} {product.unit} (было {prev_stock}, стало {product.stock_qty}). Заметка: {comment}"
        )

        # Invalidate Live KPI cache
        cache.delete('dacar_live_kpi')

        return JsonResponse({
            'success': True,
            'message': f'{action_label} успешно выполнена (+{qty} {product.unit})' if movement_type == 'IN' else f'{action_label} успешно выполнена (-{qty} {product.unit})',
            'product_id': product.id,
            'product_name': product.name,
            'stock_qty': float(product.stock_qty),
            'prev_stock': float(prev_stock),
            'unit': product.unit,
            'is_low_stock': product.is_low_stock
        })

