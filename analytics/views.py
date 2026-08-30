from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count, F, Q
from django.utils import timezone
from django.http import JsonResponse
from django.contrib.auth import get_user_model
from sales.models import SaleOrder, SaleOrderItem
from catalog.models import Product
from analytics.models import AuditLog
from decimal import Decimal
import datetime

User = get_user_model()

@login_required
def dashboard_view(request):
    # ADMIN-ONLY ACCESS TO DASHBOARD
    if not request.user.is_admin_user:
        messages.error(request, 'Доступ к главному дашборду аналитики ограничен. Раздел только для Администратора.')
        return redirect('pos')

    today = timezone.now().date()
    first_day_of_month = today.replace(day=1)

    completed_sales = SaleOrder.objects.filter(status=SaleOrder.Status.COMPLETED)

    # Today's metrics
    today_sales = completed_sales.filter(created_at__date=today)
    today_revenue = today_sales.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    today_count = today_sales.count()
    today_avg_check = (today_revenue / today_count) if today_count > 0 else Decimal('0.00')

    # Month's metrics
    month_sales = completed_sales.filter(created_at__date__gte=first_day_of_month)
    month_revenue = month_sales.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    month_count = month_sales.count()

    # Calculate Gross Profit
    today_items = SaleOrderItem.objects.filter(order__in=today_sales)
    today_cost = sum(item.quantity * item.purchase_price_snapshot for item in today_items)
    today_profit = today_revenue - today_cost

    month_items = SaleOrderItem.objects.filter(order__in=month_sales)
    month_cost = sum(item.quantity * item.purchase_price_snapshot for item in month_items)
    month_profit = month_revenue - month_cost

    # Low stock items count
    low_stock_count = Product.objects.filter(is_active=True, stock_qty__lte=F('min_stock_alert')).count()

    # CASHIER PERFORMANCE ANALYTICS ("Кто, сколько и что продал")
    cashier_stats = []
    cashiers = User.objects.filter(role=User.Role.CASHIER) | User.objects.filter(is_superuser=True)
    for c in cashiers.distinct():
        c_orders = completed_sales.filter(cashier=c)
        c_count = c_orders.count()
        c_revenue = c_orders.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        c_items = SaleOrderItem.objects.filter(order__in=c_orders)
        c_cost = sum(item.quantity * item.purchase_price_snapshot for item in c_items)
        c_profit = c_revenue - c_cost
        c_avg = (c_revenue / c_count) if c_count > 0 else Decimal('0.00')

        cashier_stats.append({
            'user': c,
            'count': c_count,
            'revenue': c_revenue,
            'profit': c_profit,
            'avg_check': c_avg
        })

    # Recent sales
    recent_orders = SaleOrder.objects.select_related('cashier').all()[:10]

    return render(request, 'analytics/dashboard.html', {
        'today_revenue': today_revenue,
        'today_profit': today_profit,
        'today_count': today_count,
        'today_avg_check': today_avg_check,
        'month_revenue': month_revenue,
        'month_profit': month_profit,
        'month_count': month_count,
        'low_stock_count': low_stock_count,
        'cashier_stats': cashier_stats,
        'recent_orders': recent_orders,
    })


@login_required
def audit_log_view(request):
    """
    Immutable Audit Trail View for Admins.
    """
    if not request.user.is_admin_user:
        messages.error(request, 'Доступ к журналу аудита ограничен. Только для Администратора.')
        return redirect('pos')

    search = request.GET.get('q', '').strip()
    action_type = request.GET.get('action_type')

    logs = AuditLog.objects.select_related('user').all()

    if search:
        logs = logs.filter(
            Q(description__icontains=search) |
            Q(user__username__icontains=search) |
            Q(user__first_name__icontains=search)
        )
    if action_type:
        logs = logs.filter(action_type=action_type)

    from django.core.paginator import Paginator

    page_number = request.GET.get('page', 1)
    paginator = Paginator(logs, 20)
    page_obj = paginator.get_page(page_number)

    return render(request, 'analytics/audit_log.html', {
        'page_obj': page_obj,
        'logs': page_obj.object_list,
        'search': search,
        'action_type': action_type,
        'action_types': AuditLog.ActionType.choices
    })


@login_required
def analytics_chart_data_api(request):
    if not request.user.is_admin_user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    today = timezone.now().date()
    completed_sales = SaleOrder.objects.filter(status=SaleOrder.Status.COMPLETED)

    # 1. Last 7 Days Revenue & Profit
    dates_label = []
    revenues = []
    profits = []

    for i in range(6, -1, -1):
        target_date = today - datetime.timedelta(days=i)
        dates_label.append(target_date.strftime('%d.%m'))
        
        day_orders = completed_sales.filter(created_at__date=target_date)
        day_rev = day_orders.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        
        day_items = SaleOrderItem.objects.filter(order__in=day_orders)
        day_cost = sum(item.quantity * item.purchase_price_snapshot for item in day_items)
        day_prof = day_rev - day_cost

        revenues.append(float(day_rev))
        profits.append(float(day_prof))

    # 2. Payment Method Distribution
    payment_counts = completed_sales.values('payment_method').annotate(count=Count('id'), total=Sum('total_amount'))
    payment_dict = {pm[0]: 0.0 for pm in SaleOrder.PaymentMethod.choices}
    for pc in payment_counts:
        payment_dict[pc['payment_method']] = float(pc['total'] or 0)

    payment_labels = [dict(SaleOrder.PaymentMethod.choices).get(k, k) for k in payment_dict.keys()]
    payment_values = list(payment_dict.values())

    # 3. Top 5 Selling Products
    top_items = SaleOrderItem.objects.filter(order__status=SaleOrder.Status.COMPLETED)\
        .values('product__name')\
        .annotate(total_qty=Sum('quantity'), total_sum=Sum('total_amount'))\
        .order_by('-total_qty')[:5]

    top_products_labels = [item['product__name'] for item in top_items]
    top_products_values = [float(item['total_qty']) for item in top_items]

    return JsonResponse({
        'sales_trend': {
            'labels': dates_label,
            'revenues': revenues,
            'profits': profits,
        },
        'payment_methods': {
            'labels': payment_labels,
            'values': payment_values,
        },
        'top_products': {
            'labels': top_products_labels,
            'values': top_products_values,
        }
    })
