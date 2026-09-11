from django.shortcuts import redirect, get_object_or_404
from django.urls import reverse
from config.rendering import render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Count, Sum
from django.utils import timezone
from sales.models import SaleOrder, SaleOrderItem
from catalog.models import Product, Category, StockMovement
from analytics.models import AuditLog
from sales.serializers import SaleCheckoutSerializer, SaleOrderSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from decimal import Decimal
from datetime import datetime, timedelta
from urllib.parse import urlencode
import json

from sales.raw_printer import print_order_direct, get_target_printer_name

@login_required
def pos_interface_view(request):
    """
    Main Cashier POS Terminal View.
    """
    categories = Category.objects.all()
    products = Product.objects.select_related('category', 'brand').filter(is_active=True, stock_qty__gt=0)

    return render(request, 'sales/pos_terminal.html', {
        'categories': categories,
        'products': products,
        'payment_methods': SaleOrder.PaymentMethod.choices,
    })


class CheckoutAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = SaleCheckoutSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            order = serializer.save()
            
            # Optional direct raw ESC/POS printing upon checkout
            auto_print = request.data.get('auto_print', False)
            print_status = None
            if auto_print:
                try:
                    p_name = get_target_printer_name()
                    print_order_direct(order, printer_name=p_name)
                    print_status = f"Чек отправлен на печать ({p_name})"
                except Exception as ex:
                    print_status = f"Ошибка автопечати: {str(ex)}"

            from django.core.cache import cache
            cache.delete('dacar_live_kpi')

            order_serializer = SaleOrderSerializer(order)
            return Response({
                'success': True,
                'message': f'Чек № {order.order_number} успешно проведен!',
                'print_status': print_status,
                'order': order_serializer.data
            }, status=status.HTTP_201_CREATED)
        return Response({
            'success': False,
            'errors': serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


class OrderDirectPrintAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        order = get_object_or_404(SaleOrder.objects.prefetch_related('items__product'), pk=pk)
        
        # Permission check: Cashier can only print their own orders unless Admin
        if not request.user.is_admin_user and order.cashier != request.user:
            return Response({'success': False, 'error': 'Доступ к чужому чеку запрещен.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            printer_name = get_target_printer_name()
            print_order_direct(order, printer_name=printer_name)
            return Response({
                'success': True,
                'message': f'Чек № {order.order_number} мгновенно отправлен на принтер ({printer_name})!',
                'printer_name': printer_name
            })
        except Exception as e:
            return Response({
                'success': False,
                'error': f'Ошибка прямого вывода на принтер: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)



@login_required
def sales_orders_list_view(request):
    search = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status')
    payment_filter = request.GET.get('payment_method')
    cashier_filter = request.GET.get('cashier_id')
    date_filter = request.GET.get('date', '').strip()
    date_from_filter = request.GET.get('date_from', '').strip()
    date_to_filter = request.GET.get('date_to', '').strip()
    event_filter = request.GET.get('event', '').strip()
    discounted_filter = request.GET.get('discounted', '').strip()
    cashier_period = request.GET.get('period', 'today')
    if cashier_period not in {'today', 'yesterday', 'week'}:
        cashier_period = 'today'

    orders = SaleOrder.objects.select_related('cashier', 'refunded_by').prefetch_related('items__product').all()

    cashier_period_label = 'сегодня'
    cashier_stats_label = 'Чеков за смену'
    shift_start = None

    # DATA ISOLATION RULE: cashiers can only see their own receipts.  The
    # period switch never weakens this ownership filter.
    if not request.user.is_admin_user:
        today = timezone.localdate()
        own_orders = orders.filter(cashier=request.user)
        first_shift_order = own_orders.filter(created_at__date=today).order_by('created_at').first()
        if first_shift_order:
            shift_start = timezone.localtime(first_shift_order.created_at).strftime('%H:%M')

        if cashier_period == 'yesterday':
            selected_day = today - timedelta(days=1)
            orders = own_orders.filter(created_at__date=selected_day)
            cashier_period_label = 'вчера'
            cashier_stats_label = 'Чеков вчера'
        elif cashier_period == 'week':
            orders = own_orders.filter(created_at__date__gte=today - timedelta(days=6), created_at__date__lte=today)
            cashier_period_label = 'за 7 дней'
            cashier_stats_label = 'Чеков за неделю'
        else:
            orders = own_orders.filter(created_at__date=today)
        date_filter = ''

    if search:
        orders = orders.filter(
            Q(order_number__icontains=search) |
            Q(items__product__name__icontains=search) |
            Q(items__product__sku__icontains=search) |
            Q(items__product__barcode=search) |
            Q(cashier__first_name__icontains=search) |
            Q(cashier__username__icontains=search)
        ).distinct()

    if status_filter:
        orders = orders.filter(status=status_filter)
    if payment_filter:
        orders = orders.filter(payment_method=payment_filter)
    if cashier_filter and request.user.is_admin_user:
        orders = orders.filter(cashier_id=cashier_filter)
    if discounted_filter == '1' and request.user.is_admin_user:
        orders = orders.filter(discount_amount__gt=0)

    selected_date = None
    selected_date_from = None
    selected_date_to = None
    if date_from_filter and date_to_filter and request.user.is_admin_user:
        try:
            selected_date_from = datetime.strptime(date_from_filter, '%Y-%m-%d').date()
            selected_date_to = datetime.strptime(date_to_filter, '%Y-%m-%d').date()
            if selected_date_from > selected_date_to:
                selected_date_from, selected_date_to = selected_date_to, selected_date_from
                date_from_filter = selected_date_from.isoformat()
                date_to_filter = selected_date_to.isoformat()
            if event_filter == 'refund':
                orders = orders.filter(
                    Q(refunded_at__date__gte=selected_date_from, refunded_at__date__lte=selected_date_to)
                    | Q(
                        refunded_at__isnull=True,
                        created_at__date__gte=selected_date_from,
                        created_at__date__lte=selected_date_to,
                    )
                )
            else:
                orders = orders.filter(
                    created_at__date__gte=selected_date_from,
                    created_at__date__lte=selected_date_to,
                )
            date_filter = ''
        except ValueError:
            date_from_filter = ''
            date_to_filter = ''
            event_filter = ''
    elif date_filter and request.user.is_admin_user:
        try:
            selected_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
            orders = orders.filter(created_at__date=selected_date)
        except ValueError:
            date_filter = ''

    order_stats = orders.aggregate(
        receipt_count=Count('id'),
        refunded_count=Count('id', filter=Q(status=SaleOrder.Status.REFUNDED)),
        # Возвращённый чек остаётся в реестре и учитывается в счётчике
        # возвратов, но не должен увеличивать чистую сумму продаж.
        total_amount=Sum(
            'total_amount',
            filter=Q(status=SaleOrder.Status.COMPLETED),
        ),
        completed_count=Count('id', filter=Q(status=SaleOrder.Status.COMPLETED)),
    )
    completed_count = order_stats['completed_count'] or 0
    total_amount = order_stats['total_amount'] or Decimal('0')
    order_stats['average_amount'] = total_amount / completed_count if completed_count else Decimal('0')

    from django.core.paginator import Paginator

    page_number = request.GET.get('page', 1)
    paginator = Paginator(orders, 15)
    page_obj = paginator.get_page(page_number)

    base_filters = {
        key: value for key, value in {
            'q': search,
            'payment_method': payment_filter,
            'cashier_id': cashier_filter,
            'date': date_filter,
            'date_from': date_from_filter,
            'date_to': date_to_filter,
            'discounted': discounted_filter,
        }.items() if value
    }
    pagination_filters = dict(base_filters)
    if status_filter:
        pagination_filters['status'] = status_filter
    if event_filter:
        pagination_filters['event'] = event_filter
    orders_url = reverse('sales_orders_list')
    all_url = f"{orders_url}?{urlencode(base_filters)}" if base_filters else orders_url
    completed_params = {**base_filters, 'status': SaleOrder.Status.COMPLETED}
    refunded_params = {**base_filters, 'status': SaleOrder.Status.REFUNDED, 'event': 'refund'}

    return render(request, 'sales/orders_list.html', {
        'page_obj': page_obj,
        'orders': page_obj.object_list,
        'search': search,
        'status_filter': status_filter,
        'payment_filter': payment_filter,
        'cashier_filter': cashier_filter,
        'date_filter': date_filter,
        'date_from_filter': date_from_filter,
        'date_to_filter': date_to_filter,
        'selected_date_from': selected_date_from,
        'selected_date_to': selected_date_to,
        'event_filter': event_filter,
        'discounted_filter': discounted_filter,
        'selected_date': selected_date,
        'cashier_period': cashier_period,
        'cashier_period_label': cashier_period_label,
        'cashier_stats_label': cashier_stats_label,
        'shift_start': shift_start,
        'order_stats': order_stats,
        'statuses': SaleOrder.Status.choices,
        'payment_methods': SaleOrder.PaymentMethod.choices,
        'orders_all_url': all_url,
        'orders_completed_url': f"{orders_url}?{urlencode(completed_params)}",
        'orders_refunded_url': f"{orders_url}?{urlencode(refunded_params)}",
        'pagination_query': urlencode(pagination_filters),
    })


@login_required
def order_detail_print_view(request, pk):
    order = get_object_or_404(SaleOrder.objects.prefetch_related('items__product'), pk=pk)
    # Check permission: Cashier can only print their own orders unless Admin
    if not request.user.is_admin_user and order.cashier != request.user:
        messages.error(request, 'Доступ к чужому чеку запрещен.')
        return redirect('sales_orders_list')

    return render(request, 'sales/print_receipt.html', {
        'order': order
    })


@login_required
def order_refund_view(request, pk):
    """
    Refund Engine for administrators and managers.
    Requires specifying a refund_reason. Creates a permanent log in AuditLog and StockMovement.
    """
    order = get_object_or_404(SaleOrder, pk=pk)

    # Determine if we're on mobile based on URL path
    is_mobile = request.path.startswith('/m/')
    orders_list_url = 'm_sales_orders_list' if is_mobile else 'sales_orders_list'

    # Permission check: ADMIN and MANAGER can refund. CASHIER cannot.
    is_mgr_or_admin = request.user.is_admin_user or getattr(request.user, 'role', '') == 'MANAGER'
    if not is_mgr_or_admin:
        messages.error(request, 'Оформление возврата разрешено только Управляющему или Администратору.')
        return redirect(orders_list_url)

    if order.status == SaleOrder.Status.REFUNDED:
        messages.warning(request, f'Чек № {order.order_number} уже был возвращен ранее.')
        return redirect(orders_list_url)

    if request.method == 'POST':
        refund_reason = request.POST.get('refund_reason', '').strip()
        if not refund_reason or len(refund_reason) < 4:
            messages.error(request, 'Пожалуйста, укажите подробную причину возврата (не менее 4 символов).')
            return redirect(orders_list_url)

        with transaction.atomic():
            order.status = SaleOrder.Status.REFUNDED
            order.refund_reason = refund_reason
            order.refunded_by = request.user
            order.refunded_at = timezone.now()
            order.save(update_fields=['status', 'refund_reason', 'refunded_by', 'refunded_at', 'updated_at'])

            # Return items to stock inventory
            for item in order.items.all():
                product = item.product
                product.stock_qty += item.quantity
                product.save(update_fields=['stock_qty', 'updated_at'])

                StockMovement.objects.create(
                    product=product,
                    movement_type=StockMovement.MovementType.RETURN,
                    quantity=item.quantity,
                    cost_price=item.purchase_price_snapshot,
                    comment=f"Возврат по чеку {order.order_number}. Причина: {refund_reason}",
                    created_by=request.user
                )

            # Audit Log Entry
            AuditLog.log(
                request,
                AuditLog.ActionType.REFUND,
                f"Оформлен возврат по чеку № {order.order_number} на сумму {order.total_amount} ₸. Провел: {request.user}. Причина: '{refund_reason}'"
            )

            from django.core.cache import cache
            cache.delete('dacar_live_kpi')

        messages.success(request, f'Возврат по чеку № {order.order_number} на сумму {order.total_amount} ₸ успешно оформлен. Остатки товаров восстановлены.')
        return redirect(orders_list_url)

    return render(request, 'sales/refund_confirm.html', {
        'order': order
    })


@login_required
def order_delete_view(request, pk):
    """
    Admin-only action to delete or cancel an order transaction.
    """
    if not request.user.is_admin_user:
        messages.error(request, 'Удаление чеков разрешено исключительно Администратору.')
        return redirect('sales_orders_list')

    order = get_object_or_404(SaleOrder, pk=pk)
    
    if request.method == 'POST':
        order_num = order.order_number
        order_sum = order.total_amount
        
        with transaction.atomic():
            AuditLog.log(
                request,
                AuditLog.ActionType.USER_ACTION,
                f"Администратор {request.user} отменил/удалил чек № {order_num} на сумму {order_sum} ₸"
            )
            order.delete()

            from django.core.cache import cache
            cache.delete('dacar_live_kpi')

        messages.success(request, f'Чек № {order_num} успешно аннулирован администратором.')
        return redirect('sales_orders_list')

    return redirect('sales_orders_list')
