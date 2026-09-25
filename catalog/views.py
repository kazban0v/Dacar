from django.shortcuts import redirect, get_object_or_404
from config.rendering import render, is_mobile_request
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, F, Sum, ExpressionWrapper, DecimalField
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
    out_of_stock = request.GET.get('out_of_stock')

    all_active_products = Product.objects.select_related('category', 'brand').filter(is_active=True)
    products = all_active_products

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
    if out_of_stock == '1':
        products = products.filter(stock_qty=0)

    from django.core.paginator import Paginator

    categories = Category.objects.all()
    brands = Brand.objects.all()

    page_number = request.GET.get('page', 1)
    paginator = Paginator(products, 15)
    page_obj = paginator.get_page(page_number)
    pagination_params = request.GET.copy()
    pagination_params.pop('page', None)

    inventory_value = Decimal('0')
    if request.user.is_admin_user:
        inventory_value = all_active_products.aggregate(
            total=Sum(
                ExpressionWrapper(
                    F('stock_qty') * F('purchase_price'),
                    output_field=DecimalField(max_digits=24, decimal_places=2),
                )
            )
        )['total'] or Decimal('0')

    return render(request, 'catalog/product_list.html', {
        'page_obj': page_obj,
        'pagination_query': pagination_params.urlencode(),
        'products': page_obj.object_list,
        'categories': categories,
        'brands': brands,
        'search': search,
        'selected_category': category_id,
        'selected_brand': brand_id,
        'low_stock_filter': low_stock,
        'out_of_stock_filter': out_of_stock,
        'total_products': products.count(),
        'all_products_count': all_active_products.count(),
        'low_stock_count': all_active_products.filter(stock_qty__lte=F('min_stock_alert')).count(),
        'out_of_stock_count': all_active_products.filter(stock_qty=0).count(),
        'inventory_value': inventory_value,
        'writeoff_reasons': StockMovement.WriteOffReason.choices,
    })


@login_required
def product_create_view(request):
    product_list_url = 'm_product_list' if is_mobile_request(request) else 'product_list'
    if not request.user.is_admin_user:
        messages.error(request, 'Добавление товаров доступно исключительно Администратору.')
        return redirect(product_list_url)

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
            return redirect(product_list_url)

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
    product_list_url = 'm_product_list' if is_mobile_request(request) else 'product_list'
    if not request.user.is_admin_user:
        messages.error(request, 'Редактирование товаров доступно исключительно Администратору.')
        return redirect(product_list_url)

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
        # Editing metadata must not overwrite stock changed by a concurrent sale.
        product.save(update_fields=['name', 'sku', 'barcode', 'category', 'brand',
            'purchase_price', 'retail_price', 'unit', 'min_stock_alert', 'updated_at'])

        AuditLog.log(
            request,
            AuditLog.ActionType.PRODUCT_UPDATE,
            f"Обновлена карточка товара '{product.name}' (Розница: {product.retail_price} ₸)"
        )

        messages.success(request, f'Товар "{product.name}" обновлен.')
        return redirect(product_list_url)

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
        import uuid
        from catalog.operations import StockActionSerializer, stock_action
        from rest_framework.exceptions import APIException
        data = request.POST.copy()
        data['action'] = data.get('movement_type')
        data['client_sync_id'] = data.get('client_sync_id') or uuid.uuid4().hex
        serializer = StockActionSerializer(data=data)
        try:
            serializer.is_valid(raise_exception=True)
            result = stock_action(request, serializer.validated_data)
            if result.get('replayed'):
                messages.info(request, 'Операция уже была выполнена. Повторного изменения остатка нет.')
            else:
                messages.success(request, result['message'])
        except APIException as exc:
            messages.error(request, str(exc.detail))
        referer = request.META.get('HTTP_REFERER')
        if referer and '/catalog/' in referer:
            return redirect(referer)
        return redirect('stock_movement')

    movements = StockMovement.objects.select_related(
        'product', 'created_by', 'reversed_by', 'reversal_of',
    ).all()[:100]
    products = Product.objects.filter(is_active=True).order_by('name')
    return render(request, 'catalog/stock_movement.html', {
        'movements': movements,
        'products': products,
        'movement_types': StockMovement.MovementType.choices,
        'writeoff_reasons': StockMovement.WriteOffReason.choices,
        'selected_product_id': request.GET.get('product', ''),
        'initial_movement_type': request.GET.get('action', ''),
        'stock_sync_id': __import__('uuid').uuid4().hex,
    })


@login_required
def reverse_writeoff_view(request, pk):
    movement_url = 'm_stock_movement' if is_mobile_request(request) else 'stock_movement'
    if not request.user.is_admin_user:
        messages.error(request, 'Отмена списания доступна исключительно Администратору.')
        return redirect('pos')
    if request.method != 'POST':
        messages.error(request, 'Используйте кнопку отмены в журнале списаний.')
        return redirect(movement_url)

    from catalog.operations import reverse_writeoff
    from rest_framework.exceptions import APIException
    try:
        result = reverse_writeoff(request, pk)
        if result['replayed']:
            messages.info(request, 'Это списание уже отменено. Остаток повторно не изменён.')
        else:
            messages.success(request, 'Списание отменено, товар возвращён на склад.')
    except APIException as exc:
        messages.error(request, str(exc.detail))
    return redirect(movement_url)


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
                serializer = ProductSerializer(product, context={'request': request})
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

        serializer = ProductSerializer(products, many=True, context={'request': request})
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
    if not request.user.is_admin_user:
        return JsonResponse({'success': False, 'error': 'Доступ разрешен только администраторам.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Метод не поддерживается'}, status=405)
    import json
    from catalog.operations import StockActionSerializer, stock_action
    from rest_framework.exceptions import APIException
    from django.core.cache import cache
    try:
        data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
        serializer = StockActionSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        result = stock_action(request, serializer.validated_data)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'success': False, 'error': 'Некорректный JSON'}, status=400)
    except APIException as exc:
        detail = exc.detail
        extra = detail if isinstance(detail, dict) else {}
        return JsonResponse({'success': False, 'error': str(detail), **extra}, status=exc.status_code)
    cache.delete('dacar_live_kpi')
    return JsonResponse(result)
