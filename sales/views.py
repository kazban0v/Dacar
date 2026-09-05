from django.shortcuts import redirect, get_object_or_404
from config.rendering import render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from sales.models import SaleOrder, SaleOrderItem
from catalog.models import Product, Category, StockMovement
from analytics.models import AuditLog
from sales.serializers import SaleCheckoutSerializer, SaleOrderSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from decimal import Decimal
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

    orders = SaleOrder.objects.select_related('cashier', 'refunded_by').prefetch_related('items__product').all()

    # DATA ISOLATION RULE: Cashiers can ONLY see their own sales for current shift/day!
    if not request.user.is_admin_user:
        today = timezone.localdate()
        orders = orders.filter(cashier=request.user, created_at__date=today)

    if search:
        orders = orders.filter(
            Q(order_number__icontains=search) |
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

    from django.core.paginator import Paginator

    page_number = request.GET.get('page', 1)
    paginator = Paginator(orders, 15)
    page_obj = paginator.get_page(page_number)

    return render(request, 'sales/orders_list.html', {
        'page_obj': page_obj,
        'orders': page_obj.object_list,
        'search': search,
        'status_filter': status_filter,
        'payment_filter': payment_filter,
        'cashier_filter': cashier_filter,
        'statuses': SaleOrder.Status.choices,
        'payment_methods': SaleOrder.PaymentMethod.choices
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
    Refund Engine: Both Cashiers and Admins can process a refund by receipt #.
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
        if not refund_reason or len(refund_reason) < 5:
            messages.error(request, 'Пожалуйста, укажите подробную причину возврата (не менее 5 символов).')
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
