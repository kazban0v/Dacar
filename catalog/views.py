from django.shortcuts import redirect, get_object_or_404
from config.rendering import render, is_mobile_request
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, F, Sum, Count, ExpressionWrapper, DecimalField
from django.http import JsonResponse
from catalog.models import Product, Category, Brand, StockMovement
from analytics.models import AuditLog
from catalog.serializers import ProductSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from decimal import Decimal, InvalidOperation
import random


def _product_form_context(request, *, product=None, values=None):
    """Shared context for the create/edit form, including values after a validation error."""
    return {
        'product': product,
        'categories': Category.objects.all(),
        'brands': Brand.objects.all(),
        'units': Product.UNIT_CHOICES,
        'initial_barcode': request.GET.get('barcode', '').strip(),
        'form_values': values or {},
    }


def _product_form_values(request):
    return {
        key: request.POST.get(key, '').strip()
        for key in ('name', 'sku', 'barcode', 'category', 'brand', 'new_brand', 'purchase_price',
                    'retail_price', 'unit', 'stock_qty', 'min_stock_alert')
    }


def _valid_product_numbers(values, *, include_stock=False):
    fields = ('purchase_price', 'retail_price', 'min_stock_alert')
    if include_stock:
        fields += ('stock_qty',)
    parsed = {}
    try:
        for field in fields:
            parsed[field] = Decimal(values.get(field) or '0')
            if parsed[field] < 0:
                raise ValueError(field)
    except (InvalidOperation, ValueError):
        return None
    return parsed


def _valid_product_image(request):
    image = request.FILES.get('image')
    if not image:
        return True
    allowed_types = {'image/jpeg', 'image/png', 'image/webp'}
    return image.size <= 10 * 1024 * 1024 and image.content_type in allowed_types

@login_required
def product_list_view(request):
    search = request.GET.get('q', '').strip()
    category_id = request.GET.get('category')
    brand_id = request.GET.get('brand')
    low_stock = request.GET.get('low_stock')
    out_of_stock = request.GET.get('out_of_stock')
    quality_filter = request.GET.get('quality', '')

    all_active_products = Product.objects.select_related('category', 'brand').filter(is_active=True)
    products = all_active_products
    duplicate_names = list(
        all_active_products.values('name').annotate(total=Count('id')).filter(total__gt=1)
        .values_list('name', flat=True)
    )

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
    if quality_filter == 'duplicate_name':
        products = products.filter(name__in=duplicate_names)
    elif quality_filter == 'missing_barcode':
        products = products.filter(Q(barcode__isnull=True) | Q(barcode=''))
    elif quality_filter == 'missing_purchase':
        products = products.filter(purchase_price__lte=0)
    elif quality_filter == 'missing_category':
        products = products.filter(category__isnull=True)
    elif quality_filter == 'missing_image':
        products = products.filter(Q(image__isnull=True) | Q(image=''))

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
        'quality_filter': quality_filter,
        'quality_counts': {
            'duplicate_name': len(duplicate_names),
            'missing_barcode': all_active_products.filter(Q(barcode__isnull=True) | Q(barcode='')).count(),
            'missing_purchase': all_active_products.filter(purchase_price__lte=0).count(),
            'missing_category': all_active_products.filter(category__isnull=True).count(),
            'missing_image': all_active_products.filter(Q(image__isnull=True) | Q(image='')).count(),
        },
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
        values = _product_form_values(request)
        name = values['name']
        sku = values['sku']
        barcode = values['barcode']
        category_id = values['category']
        brand_input = values['brand']
        if brand_input == '__NEW__':
            brand_input = request.POST.get('new_brand', '').strip()
            if not brand_input:
                messages.error(request, 'Введите название нового бренда или выберите существующий.')
                return render(request, 'catalog/product_form.html', _product_form_context(request, values=values))
        numbers = _valid_product_numbers(values, include_stock=True)

        if not name:
            messages.error(request, 'Укажите наименование товара.')
            return render(request, 'catalog/product_form.html', _product_form_context(request, values=values))
        if not category_id or not Category.objects.filter(id=category_id).exists():
            messages.error(request, 'Выберите категорию товара. Товар не будет автоматически отнесён к чужой категории.')
            return render(request, 'catalog/product_form.html', _product_form_context(request, values=values))
        if numbers is None:
            messages.error(request, 'Цены и остаток должны быть неотрицательными числами.')
            return render(request, 'catalog/product_form.html', _product_form_context(request, values=values))
        if not _valid_product_image(request):
            messages.error(request, 'Загрузите JPEG, PNG или WebP размером не больше 10 МБ.')
            return render(request, 'catalog/product_form.html', _product_form_context(request, values=values))

        if not barcode:
            barcode = f"200{random.randint(100000000, 999999999)}"

        if not sku:
            base_code = barcode[-6:] if len(barcode) >= 6 else str(random.randint(100000, 999999))
            sku = f"DAC-{base_code}"
            counter = 1
            while Product.objects.filter(sku=sku).exists():
                sku = f"DAC-{base_code}-{counter}"
                counter += 1

        if Product.objects.filter(name__iexact=name).exists():
            messages.error(request, 'Товар с таким названием уже есть в каталоге. Откройте существующую карточку и проверьте штрихкод.')
        elif Product.objects.filter(barcode=barcode).exists():
            messages.error(request, f'Товар со штрихкодом {barcode} уже существует в базе.')
        elif Product.objects.filter(sku=sku).exists():
            messages.error(request, f'Товар с артикулом {sku} уже существует.')
        else:
            category = Category.objects.get(id=category_id)

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
                purchase_price=numbers['purchase_price'],
                retail_price=numbers['retail_price'],
                unit=values['unit'] or 'шт',
                stock_qty=numbers['stock_qty'],
                min_stock_alert=numbers['min_stock_alert'],
                image=request.FILES.get('image'),
            )

            # Log stock movement if initial stock > 0
            if numbers['stock_qty'] > Decimal('0'):
                StockMovement.objects.create(
                    product=product,
                    movement_type=StockMovement.MovementType.IN,
                    quantity=numbers['stock_qty'],
                    cost_price=numbers['purchase_price'],
                    comment='Первичный ввод товара на склад',
                    created_by=request.user
                )

            from analytics.audit_helpers import build_change_diff, diff_description
            from catalog.templatetags.dacar_format import format_tenge
            diff = build_change_diff(
                {
                    'name': None,
                    'sku': None,
                    'barcode': None,
                    'purchase_price': None,
                    'retail_price': None,
                },
                {
                    'name': product.name,
                    'sku': product.sku,
                    'barcode': product.barcode,
                    'purchase_price': product.purchase_price,
                    'retail_price': product.retail_price,
                },
            )
            AuditLog.log(
                request,
                AuditLog.ActionType.PRODUCT_CREATE,
                f"Создан товар «{product.name}». Розница: {format_tenge(product.retail_price)} ₸. {diff_description(diff)}",
                metadata={
                    'action': 'product_create',
                    'product_id': product.pk,
                    'product_name': product.name,
                    **diff,
                },
            )

            messages.success(request, f'Товар "{product.name}" успешно добавлен.')
            return redirect(product_list_url)

    return render(request, 'catalog/product_form.html', _product_form_context(
        request, values=_product_form_values(request) if request.method == 'POST' else None
    ))


@login_required
def product_edit_view(request, pk):
    product_list_url = 'm_product_list' if is_mobile_request(request) else 'product_list'
    if not request.user.is_admin_user:
        messages.error(request, 'Редактирование товаров доступно исключительно Администратору.')
        return redirect(product_list_url)

    product = get_object_or_404(Product, pk=pk)

    if request.method == 'POST':
        values = _product_form_values(request)
        numbers = _valid_product_numbers(values)
        category = Category.objects.filter(id=values['category']).first() if values['category'] else None
        if not values['name']:
            messages.error(request, 'Укажите наименование товара.')
            return render(request, 'catalog/product_form.html', _product_form_context(request, product=product, values=values))
        if not category:
            messages.error(request, 'Выберите категорию товара. Она не подставляется автоматически.')
            return render(request, 'catalog/product_form.html', _product_form_context(request, product=product, values=values))
        if numbers is None:
            messages.error(request, 'Цены и порог остатка должны быть неотрицательными числами.')
            return render(request, 'catalog/product_form.html', _product_form_context(request, product=product, values=values))
        if not _valid_product_image(request):
            messages.error(request, 'Загрузите JPEG, PNG или WebP размером не больше 10 МБ.')
            return render(request, 'catalog/product_form.html', _product_form_context(request, product=product, values=values))
        if Product.objects.filter(name__iexact=values['name']).exclude(pk=product.pk).exists():
            messages.error(request, 'Товар с таким названием уже есть в каталоге. Объедините дубликат вручную, не создавая вторую карточку.')
            return render(request, 'catalog/product_form.html', _product_form_context(request, product=product, values=values))
        if values['barcode'] and Product.objects.filter(barcode=values['barcode']).exclude(pk=product.pk).exists():
            messages.error(request, f'Товар со штрихкодом {values["barcode"]} уже существует в базе.')
            return render(request, 'catalog/product_form.html', _product_form_context(request, product=product, values=values))
        if values['sku'] and Product.objects.filter(sku=values['sku']).exclude(pk=product.pk).exists():
            messages.error(request, f'Товар с артикулом {values["sku"]} уже существует.')
            return render(request, 'catalog/product_form.html', _product_form_context(request, product=product, values=values))
        old_values = {
            'name': product.name,
            'sku': product.sku,
            'barcode': product.barcode,
            'category': product.category.name if product.category else '',
            'brand': product.brand.name if product.brand else '',
            'purchase_price': product.purchase_price,
            'retail_price': product.retail_price,
            'unit': product.unit,
            'min_stock_alert': product.min_stock_alert,
        }
        product.name = values['name']
        sku_input = values['sku']
        if sku_input:
            product.sku = sku_input
        product.barcode = values['barcode']
        brand_input = values['brand']
        if brand_input == '__NEW__':
            brand_input = request.POST.get('new_brand', '').strip()
            if not brand_input:
                messages.error(request, 'Введите название нового бренда или выберите существующий.')
                return render(request, 'catalog/product_form.html', _product_form_context(request, product=product, values=values))
        product.category = category
        
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
        
        product.purchase_price = numbers['purchase_price']
        product.retail_price = numbers['retail_price']
        product.unit = values['unit'] or 'шт'
        product.min_stock_alert = numbers['min_stock_alert']
        if request.FILES.get('image'):
            product.image = request.FILES['image']
        # Editing metadata must not overwrite stock changed by a concurrent sale.
        update_fields = ['name', 'sku', 'barcode', 'category', 'brand',
            'purchase_price', 'retail_price', 'unit', 'min_stock_alert', 'updated_at']
        if request.FILES.get('image'):
            update_fields.append('image')
        product.save(update_fields=update_fields)

        new_values = {
            'name': product.name,
            'sku': product.sku,
            'barcode': product.barcode,
            'category': product.category.name if product.category else '',
            'brand': product.brand.name if product.brand else '',
            'purchase_price': product.purchase_price,
            'retail_price': product.retail_price,
            'unit': product.unit,
            'min_stock_alert': product.min_stock_alert,
        }
        from analytics.audit_helpers import build_change_diff, diff_description
        diff = build_change_diff(old_values, new_values)
        description = diff_description(diff) if diff['changes'] else 'Изменены параметры без финансовых изменений.'
        AuditLog.log(
            request,
            AuditLog.ActionType.PRODUCT_UPDATE,
            f"Карточка товара «{product.name}» изменена. {description}",
            metadata={
                'action': 'product_update',
                'product_id': product.pk,
                'product_name': product.name,
                **diff,
            },
        )

        messages.success(request, f'Товар "{product.name}" обновлен.')
        return redirect(product_list_url)

    return render(request, 'catalog/product_form.html', _product_form_context(request, product=product))


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

    from django.core.paginator import Paginator
    movements_queryset = StockMovement.objects.select_related(
        'product', 'created_by', 'reversed_by', 'reversal_of',
    ).all()
    page_obj = Paginator(movements_queryset, 50).get_page(request.GET.get('page', 1))
    products = Product.objects.filter(is_active=True).order_by('name')
    return render(request, 'catalog/stock_movement.html', {
        'movements': page_obj.object_list,
        'page_obj': page_obj,
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
