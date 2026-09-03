from django.shortcuts import redirect, get_object_or_404
from config.rendering import render
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

    today = timezone.localdate()
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
    # Get all users who have completed sales, plus all active cashiers and admins
    active_cashiers = list(User.objects.filter(role__in=[User.Role.CASHIER, User.Role.ADMIN]))
    order_cashier_ids = completed_sales.exclude(cashier=None).values_list('cashier_id', flat=True).distinct()
    order_cashiers = list(User.objects.filter(id__in=order_cashier_ids))
    
    # Combine and deduplicate
    all_cashiers = list({c.id: c for c in active_cashiers + order_cashiers}.values())
    
    # Also add None to represent deleted cashiers if there are any orders without a cashier
    has_null_cashiers = completed_sales.filter(cashier=None).exists()
    if has_null_cashiers:
        all_cashiers.append(None)

    for c in all_cashiers:
        c_orders = completed_sales.filter(cashier=c)
        c_count = c_orders.count()
        if c_count == 0 and c is not None:
            continue # Skip users with no sales if they are just in the active list, wait, let's show them with 0
            
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
    
    # Sort by revenue descending
    cashier_stats.sort(key=lambda x: x['revenue'], reverse=True)

    # Payment breakdown
    payment_stats = today_sales.values('payment_method').annotate(
        total=Sum('total_amount'),
        cnt=Count('id')
    )
    pm_sums = {'TRANSFER': 0.0, 'QR': 0.0, 'CASH': 0.0, 'CARD': 0.0, 'MIXED': 0.0}
    total_pm_sum = 0.0
    for p in payment_stats:
        m = p['payment_method']
        val = float(p['total'] or 0)
        pm_sums[m] = val
        total_pm_sum += val

    qr_total = pm_sums.get('TRANSFER', 0.0) + pm_sums.get('QR', 0.0)
    cash_total = pm_sums.get('CASH', 0.0)
    card_total = pm_sums.get('CARD', 0.0) + pm_sums.get('MIXED', 0.0)

    if total_pm_sum > 0:
        qr_pct = int(round((qr_total / total_pm_sum) * 100))
        cash_pct = int(round((cash_total / total_pm_sum) * 100))
        card_pct = max(0, 100 - qr_pct - cash_pct)
    else:
        qr_pct = 0
        cash_pct = 0
        card_pct = 0

    payment_breakdown = {
        'qr_sum': qr_total,
        'qr_pct': qr_pct,
        'cash_sum': cash_total,
        'cash_pct': cash_pct,
        'card_sum': card_total,
        'card_pct': card_pct,
        'total_sum': total_pm_sum,
    }

    # Target calculation
    yesterday = today - datetime.timedelta(days=1)
    yesterday_sales = completed_sales.filter(created_at__date=yesterday)
    yesterday_rev = float(yesterday_sales.aggregate(total_rev=Sum('total_amount'))['total_rev'] or 0)
    growth_pct = 0.0
    if yesterday_rev > 0:
        growth_pct = round(((float(today_revenue) - yesterday_rev) / yesterday_rev) * 100, 1)
        target_pct = min(100, int(round((float(today_revenue) / (yesterday_rev * 1.30)) * 100)))
    elif float(today_revenue) > 0:
        growth_pct = 100.0
        target_pct = 100
    else:
        target_pct = 0

    # Hourly sales for today (24h array)
    hourly_data = [0.0] * 24
    for s in today_sales:
        hour = timezone.localtime(s.created_at).hour
        hourly_data[hour] += float(s.total_amount)

    # Recent sales
    recent_orders = SaleOrder.objects.select_related('cashier').all()[:10]

    return render(request, 'analytics/dashboard.html', {
        'today_revenue': today_revenue,
        'yesterday_revenue': yesterday_rev,
        'growth_percent': growth_pct,
        'target_percent': target_pct,
        'today_profit': today_profit,
        'today_count': today_count,
        'today_avg_check': today_avg_check,
        'month_revenue': month_revenue,
        'month_profit': month_profit,
        'month_count': month_count,
        'low_stock_count': low_stock_count,
        'payment_breakdown': payment_breakdown,
        'hourly_sales': hourly_data,
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

    today = timezone.localdate()
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


@login_required
def live_kpi_api(request):
    """
    High-performance, cached live KPI stream for DACAR Mobile Hub (< 10ms response).
    Returns real-time revenue, growth vs yesterday, check count, avg check, and last order.
    """
    if not request.user.is_admin_user:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    from django.core.cache import cache
    cached_data = cache.get('dacar_live_kpi')
    if cached_data:
        return JsonResponse(cached_data)

    today = timezone.localdate()
    yesterday = today - datetime.timedelta(days=1)
    
    completed_sales = SaleOrder.objects.filter(status=SaleOrder.Status.COMPLETED)
    
    # Today's metrics
    today_sales = completed_sales.filter(created_at__date=today)
    today_agg = today_sales.aggregate(
        total_rev=Sum('total_amount'),
        cnt=Count('id')
    )
    today_rev = float(today_agg['total_rev'] or 0)
    today_cnt = today_agg['cnt'] or 0
    today_avg = round(today_rev / today_cnt, 2) if today_cnt > 0 else 0.0

    # Yesterday's metrics
    yesterday_sales = completed_sales.filter(created_at__date=yesterday)
    yesterday_rev = float(yesterday_sales.aggregate(total_rev=Sum('total_amount'))['total_rev'] or 0)
    
    growth_pct = 0.0
    if yesterday_rev > 0:
        growth_pct = round(((today_rev - yesterday_rev) / yesterday_rev) * 100, 1)
    elif today_rev > 0:
        growth_pct = 100.0

    # Low stock alert count
    low_stock_count = Product.objects.filter(is_active=True, stock_qty__lte=F('min_stock_alert')).count()

    # Last completed order
    last_order_obj = completed_sales.select_related('cashier').order_by('-id').first()
    last_order_data = None
    if last_order_obj:
        last_order_data = {
            'id': last_order_obj.id,
            'order_number': last_order_obj.order_number,
            'cashier_name': last_order_obj.cashier.get_full_name() or last_order_obj.cashier.username if last_order_obj.cashier else 'Кассир',
            'total_amount': float(last_order_obj.total_amount),
            'payment_method_display': last_order_obj.get_payment_method_display(),
            'time': timezone.localtime(last_order_obj.created_at).strftime('%H:%M:%S')
        }

    # Profit today
    today_items = SaleOrderItem.objects.filter(order__in=today_sales)
    today_cost = sum(item.quantity * item.purchase_price_snapshot for item in today_items)
    today_profit = float(today_rev - float(today_cost))

    # Payment breakdown
    payment_stats = today_sales.values('payment_method').annotate(
        total=Sum('total_amount'),
        cnt=Count('id')
    )
    pm_sums = {'TRANSFER': 0.0, 'QR': 0.0, 'CASH': 0.0, 'CARD': 0.0, 'MIXED': 0.0}
    total_pm_sum = 0.0
    for p in payment_stats:
        m = p['payment_method']
        val = float(p['total'] or 0)
        pm_sums[m] = val
        total_pm_sum += val

    qr_total = pm_sums.get('TRANSFER', 0.0) + pm_sums.get('QR', 0.0)
    cash_total = pm_sums.get('CASH', 0.0)
    card_total = pm_sums.get('CARD', 0.0) + pm_sums.get('MIXED', 0.0)

    if total_pm_sum > 0:
        qr_pct = int(round((qr_total / total_pm_sum) * 100))
        cash_pct = int(round((cash_total / total_pm_sum) * 100))
        card_pct = max(0, 100 - qr_pct - cash_pct)
    else:
        qr_pct = 0
        cash_pct = 0
        card_pct = 0

    target_pct = 100
    if yesterday_rev > 0:
        target_pct = min(100, int(round((today_rev / (yesterday_rev * 1.30)) * 100)))
    elif today_rev > 0:
        target_pct = 100
    else:
        target_pct = 0

    # Hourly sales for today (24h array)
    hourly_data = [0.0] * 24
    for s in today_sales:
        hour = timezone.localtime(s.created_at).hour
        hourly_data[hour] += float(s.total_amount)

    response_payload = {
        'revenue_today': today_rev,
        'revenue_yesterday': yesterday_rev,
        'growth_percent': growth_pct,
        'target_percent': target_pct,
        'profit_today': today_profit,
        'orders_count_today': today_cnt,
        'avg_check_today': today_avg,
        'low_stock_count': low_stock_count,
        'payment_breakdown': {
            'qr_sum': qr_total,
            'qr_pct': qr_pct,
            'cash_sum': cash_total,
            'cash_pct': cash_pct,
            'card_sum': card_total,
            'card_pct': card_pct,
        },
        'last_order': last_order_data,
        'hourly_sales': hourly_data,
        'server_time': timezone.localtime(timezone.now()).strftime('%H:%M:%S')
    }

    cache.set('dacar_live_kpi', response_payload, timeout=10)
    return JsonResponse(response_payload)
