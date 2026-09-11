from django.shortcuts import redirect
from config.rendering import render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum, Count, F, Q, DecimalField
from django.utils import timezone
from django.http import JsonResponse, HttpResponse
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from sales.models import SaleOrder, SaleOrderItem
from catalog.models import Product, Category
from analytics.models import AuditLog, NotificationState
from decimal import Decimal
import datetime
import csv
import json
from urllib.parse import urlencode

User = get_user_model()

ZERO = Decimal('0.00')
SALE_STATUSES = (SaleOrder.Status.COMPLETED, SaleOrder.Status.REFUNDED)


def _money_sum(queryset, field='total_amount'):
    return queryset.aggregate(total=Sum(field))['total'] or ZERO


def _item_cost(queryset):
    return queryset.aggregate(
        total=Sum(
            F('quantity') * F('purchase_price_snapshot'),
            output_field=DecimalField(max_digits=20, decimal_places=2),
        )
    )['total'] or ZERO


def _resolve_period(request, default='day'):
    """Return a validated reporting range and labels shared by UI, API and CSV."""
    today = timezone.localdate()
    period = request.GET.get('period', default)
    if period not in {'day', 'week', 'month', 'year', 'custom'}:
        period = default

    if period == 'week':
        start, title, label = today - datetime.timedelta(days=6), 'Неделя', 'за неделю'
    elif period == 'month':
        start, title, label = today.replace(day=1), 'Месяц', 'за месяц'
    elif period == 'year':
        start, title, label = today.replace(month=1, day=1), 'Год', 'за год'
    elif period == 'custom':
        try:
            start = datetime.date.fromisoformat(request.GET.get('date_from', ''))
            end = datetime.date.fromisoformat(request.GET.get('date_to', ''))
        except ValueError:
            start, end = today, today
        if start > end:
            start, end = end, start
        end = min(end, today)
        start = min(start, end)
        # A bounded range keeps accidental multi-year reports responsive.
        if (end - start).days > 366:
            start = end - datetime.timedelta(days=366)
        return {
            'period': period,
            'start': start,
            'end': end,
            'title': 'Период',
            'label': f'с {start:%d.%m.%Y} по {end:%d.%m.%Y}',
        }
    else:
        start, title, label = today, 'Сегодня', 'сегодня'

    return {'period': period, 'start': start, 'end': today, 'title': title, 'label': label}


def _refunds_in_range(start, end):
    """Refunds are reported on the day they happened, with a legacy fallback."""
    return SaleOrder.objects.filter(status=SaleOrder.Status.REFUNDED).filter(
        Q(refunded_at__date__gte=start, refunded_at__date__lte=end)
        | Q(refunded_at__isnull=True, created_at__date__gte=start, created_at__date__lte=end)
    )


def _sales_summary(start, end):
    # Refunded receipts stay in gross sales history; the refund is a separate
    # negative event on refunded_at. This prevents old sales from disappearing.
    sales = SaleOrder.objects.filter(
        status__in=SALE_STATUSES,
        created_at__date__gte=start,
        created_at__date__lte=end,
    )
    refunds = _refunds_in_range(start, end)
    cohort_refunds = refunds.filter(created_at__date__gte=start, created_at__date__lte=end)
    prior_period_refunds = refunds.exclude(created_at__date__gte=start, created_at__date__lte=end)
    items = SaleOrderItem.objects.filter(order__in=sales)
    refund_items = SaleOrderItem.objects.filter(order__in=refunds)

    gross_revenue = _money_sum(sales)
    refund_amount = _money_sum(refunds)
    net_revenue = gross_revenue - refund_amount
    gross_cost = _item_cost(items)
    returned_cost = _item_cost(refund_items)
    net_cost = gross_cost - returned_cost
    gross_profit = net_revenue - net_cost
    subtotal = _money_sum(sales, 'subtotal_amount')
    discounts = _money_sum(sales, 'discount_amount')
    checks = sales.count()
    refund_count = refunds.count()
    cohort_refund_count = cohort_refunds.count()
    prior_period_refund_count = prior_period_refunds.count()
    prior_period_refund_amount = _money_sum(prior_period_refunds)
    units_sold = items.aggregate(total=Sum('quantity'))['total'] or ZERO
    units_returned = refund_items.aggregate(total=Sum('quantity'))['total'] or ZERO

    return {
        'sales': sales,
        'refunds': refunds,
        'gross_revenue': gross_revenue,
        'refund_amount': refund_amount,
        'net_revenue': net_revenue,
        'gross_cost': gross_cost,
        'returned_cost': returned_cost,
        'net_cost': net_cost,
        'gross_profit': gross_profit,
        'subtotal': subtotal,
        'discounts': discounts,
        'discount_rate': (discounts / subtotal * 100) if subtotal else ZERO,
        'check_count': checks,
        'refund_count': refund_count,
        # This rate compares like with like: only receipts created in the
        # selected period. Refunds of older receipts remain visible separately.
        'refund_rate': (Decimal(cohort_refund_count) / Decimal(checks) * 100) if checks else ZERO,
        'cohort_refund_count': cohort_refund_count,
        'prior_period_refund_count': prior_period_refund_count,
        'prior_period_refund_amount': prior_period_refund_amount,
        'average_check': (gross_revenue / checks) if checks else ZERO,
        # A percentage margin is not meaningful when net revenue is zero or
        # negative. The money result remains visible, but the ratio is omitted.
        'margin': (gross_profit / net_revenue * 100) if net_revenue > 0 else None,
        'margin_available': net_revenue > 0,
        'returns_exceed_sales': refund_amount > gross_revenue,
        'units_sold': units_sold,
        'units_returned': units_returned,
        'net_units': units_sold - units_returned,
    }


def _inventory_summary():
    active = Product.objects.filter(is_active=True)
    values = active.aggregate(
        stock_cost=Sum(F('stock_qty') * F('purchase_price'), output_field=DecimalField(max_digits=20, decimal_places=2)),
        stock_retail=Sum(F('stock_qty') * F('retail_price'), output_field=DecimalField(max_digits=20, decimal_places=2)),
    )
    out_of_stock = active.filter(stock_qty__lte=0).count()
    low_stock = active.filter(stock_qty__gt=0, stock_qty__lte=F('min_stock_alert')).count()
    stock_cost = values['stock_cost'] or ZERO
    stock_retail = values['stock_retail'] or ZERO
    return {
        'active_count': active.count(),
        'out_of_stock_count': out_of_stock,
        'low_stock_count': low_stock,
        'attention_count': out_of_stock + low_stock,
        'stock_cost': stock_cost,
        'stock_retail': stock_retail,
        'potential_profit': stock_retail - stock_cost,
        'without_cost_count': active.filter(purchase_price__lte=0).count(),
        'without_price_count': active.filter(retail_price__lte=0).count(),
        'without_barcode_count': active.filter(Q(barcode='') | Q(barcode__isnull=True)).count(),
        'without_category_count': active.filter(category__isnull=True).count(),
    }


def _period_comparison(current, previous, *, lower_is_better=False):
    """Build an honest, presentation-ready comparison with the prior range."""
    current = Decimal(current or 0)
    previous = Decimal(previous or 0)
    if previous == 0:
        if current == 0:
            percent, text, direction = Decimal('0'), 'Без изменений', 'flat'
        else:
            percent = Decimal('100') if current > 0 else Decimal('-100')
            text, direction = ('Новое значение', 'up') if current > 0 else ('Снижение', 'down')
    else:
        percent = (current - previous) / abs(previous) * 100
        direction = 'up' if percent > 0 else 'down' if percent < 0 else 'flat'
        sign = '+' if percent > 0 else ''
        text = f'{sign}{percent.quantize(Decimal("0.1"))}%'

    if direction == 'flat':
        tone = 'neutral'
    elif (direction == 'down' and lower_is_better) or (direction == 'up' and not lower_is_better):
        tone = 'good'
    else:
        tone = 'bad'
    return {'percent': percent, 'text': text, 'direction': direction, 'tone': tone}


def _analytics_report(start, end):
    summary = _sales_summary(start, end)
    sales = summary['sales']

    payment_rows = list(sales.values('payment_method').annotate(
        amount=Sum('total_amount'), checks=Count('id')
    ).order_by('-amount'))
    payment_names = dict(SaleOrder.PaymentMethod.choices)
    for row in payment_rows:
        row['name'] = payment_names.get(row['payment_method'], row['payment_method'])
        row['share'] = (row['amount'] / summary['gross_revenue'] * 100) if summary['gross_revenue'] else ZERO

    gross_product_rows = list(SaleOrderItem.objects.filter(order__in=sales).values(
        'product_id', 'product__name', 'product__category__icon'
    ).annotate(
        total_qty=Sum('quantity'),
        total_sum=Sum('total_amount'),
        total_cost=Sum(F('quantity') * F('purchase_price_snapshot'), output_field=DecimalField(max_digits=20, decimal_places=2)),
    ))
    returned_product_rows = SaleOrderItem.objects.filter(order__in=summary['refunds']).values(
        'product_id', 'product__name', 'product__category__icon'
    ).annotate(
        total_qty=Sum('quantity'),
        total_sum=Sum('total_amount'),
        total_cost=Sum(F('quantity') * F('purchase_price_snapshot'), output_field=DecimalField(max_digits=20, decimal_places=2)),
    )
    product_map = {row['product_id']: row for row in gross_product_rows}
    for returned in returned_product_rows:
        row = product_map.setdefault(returned['product_id'], {
            'product_id': returned['product_id'],
            'product__name': returned['product__name'],
            'product__category__icon': returned['product__category__icon'],
            'total_qty': ZERO, 'total_sum': ZERO, 'total_cost': ZERO,
        })
        row['total_qty'] = (row['total_qty'] or ZERO) - (returned['total_qty'] or ZERO)
        row['total_sum'] = (row['total_sum'] or ZERO) - (returned['total_sum'] or ZERO)
        row['total_cost'] = (row['total_cost'] or ZERO) - (returned['total_cost'] or ZERO)
    top_products = sorted(product_map.values(), key=lambda row: row['total_sum'] or ZERO, reverse=True)[:10]
    for row in top_products:
        row['profit'] = (row['total_sum'] or ZERO) - (row['total_cost'] or ZERO)

    gross_category_rows = list(SaleOrderItem.objects.filter(order__in=sales).values(
        'product__category_id', 'product__category__name'
    ).annotate(
        total_qty=Sum('quantity'),
        revenue=Sum('total_amount'),
        cost=Sum(F('quantity') * F('purchase_price_snapshot'), output_field=DecimalField(max_digits=20, decimal_places=2)),
    ))
    returned_category_rows = SaleOrderItem.objects.filter(order__in=summary['refunds']).values(
        'product__category_id', 'product__category__name'
    ).annotate(
        total_qty=Sum('quantity'),
        revenue=Sum('total_amount'),
        cost=Sum(F('quantity') * F('purchase_price_snapshot'), output_field=DecimalField(max_digits=20, decimal_places=2)),
    )
    category_map = {row['product__category_id']: row for row in gross_category_rows}
    for returned in returned_category_rows:
        row = category_map.setdefault(returned['product__category_id'], {
            'product__category_id': returned['product__category_id'],
            'product__category__name': returned['product__category__name'],
            'total_qty': ZERO, 'revenue': ZERO, 'cost': ZERO,
        })
        row['total_qty'] = (row['total_qty'] or ZERO) - (returned['total_qty'] or ZERO)
        row['revenue'] = (row['revenue'] or ZERO) - (returned['revenue'] or ZERO)
        row['cost'] = (row['cost'] or ZERO) - (returned['cost'] or ZERO)
    category_rows = sorted(category_map.values(), key=lambda row: row['revenue'] or ZERO, reverse=True)
    for row in category_rows:
        row['name'] = row['product__category__name'] or 'Без категории'
        row['profit'] = (row['revenue'] or ZERO) - (row['cost'] or ZERO)

    cashier_rows = []
    grouped_cashiers = list(sales.values('cashier_id').annotate(
        revenue=Sum('total_amount'), checks=Count('id')
    ))
    grouped_refunds = list(summary['refunds'].values('cashier_id').annotate(
        revenue=Sum('total_amount'), checks=Count('id')
    ))
    cashier_map = {row['cashier_id']: {'cashier_id': row['cashier_id'], 'gross_revenue': row['revenue'] or ZERO, 'checks': row['checks'], 'refund_amount': ZERO, 'refunds': 0} for row in grouped_cashiers}
    for refunded in grouped_refunds:
        row = cashier_map.setdefault(refunded['cashier_id'], {'cashier_id': refunded['cashier_id'], 'gross_revenue': ZERO, 'checks': 0, 'refund_amount': ZERO, 'refunds': 0})
        row['refund_amount'] = refunded['revenue'] or ZERO
        row['refunds'] = refunded['checks']
    cashier_users = User.objects.in_bulk([cashier_id for cashier_id in cashier_map if cashier_id])
    for row in cashier_map.values():
        cashier_id = row['cashier_id']
        gross_orders = sales.filter(cashier_id=cashier_id)
        refund_orders = summary['refunds'].filter(cashier_id=cashier_id)
        gross_cost = _item_cost(SaleOrderItem.objects.filter(order__in=gross_orders))
        returned_cost = _item_cost(SaleOrderItem.objects.filter(order__in=refund_orders))
        cashier = cashier_users.get(cashier_id)
        net_revenue = row['gross_revenue'] - row['refund_amount']
        cashier_rows.append({
            'cashier_id': cashier_id,
            'user': cashier,
            'name': (cashier.get_full_name() or cashier.username) if cashier else 'Удалённый кассир',
            'revenue': net_revenue,
            'gross_revenue': row['gross_revenue'],
            'refund_amount': row['refund_amount'],
            'refunds': row['refunds'],
            'checks': row['checks'],
            'average_check': (row['gross_revenue'] / row['checks']) if row['checks'] else ZERO,
            'profit': net_revenue - (gross_cost - returned_cost),
        })
    cashier_rows.sort(key=lambda row: row['revenue'], reverse=True)

    summary.update({
        'inventory': _inventory_summary(),
        'payment_rows': payment_rows,
        'top_products': top_products,
        'category_rows': category_rows,
        'cashier_rows': cashier_rows,
        'recent_orders': SaleOrder.objects.select_related('cashier').filter(
            Q(created_at__date__gte=start, created_at__date__lte=end)
            | Q(refunded_at__date__gte=start, refunded_at__date__lte=end)
        ).distinct().order_by('-created_at')[:10],
        'low_stock_products': Product.objects.filter(
            is_active=True, stock_qty__lte=F('min_stock_alert')
        ).order_by('stock_qty', 'name')[:20],
    })
    return summary


def _notification_feed(user, state):
    """Build the notification centre from current business records."""
    type_config = {
        AuditLog.ActionType.SALE: ('Продажа проведена', 'fa-receipt', 'green', 'sales_orders_list'),
        AuditLog.ActionType.REFUND: ('Оформлен возврат', 'fa-rotate-left', 'red', 'sales_orders_list'),
        AuditLog.ActionType.STOCK_IN: ('Поступление на склад', 'fa-arrow-down', 'cyan', 'product_list'),
        AuditLog.ActionType.STOCK_OUT: ('Списание со склада', 'fa-arrow-up', 'orange', 'product_list'),
        AuditLog.ActionType.STOCK_ADJUST: ('Остаток скорректирован', 'fa-boxes-stacked', 'orange', 'product_list'),
        AuditLog.ActionType.PRODUCT_CREATE: ('Добавлен новый товар', 'fa-box-open', 'purple', 'product_list'),
        AuditLog.ActionType.PRODUCT_UPDATE: ('Карточка товара изменена', 'fa-pen', 'purple', 'product_list'),
        AuditLog.ActionType.USER_ACTION: ('Изменение персонала', 'fa-user-gear', 'cyan', 'users_list'),
    }

    audit_logs = AuditLog.objects.select_related('user').order_by('-created_at')
    if not user.is_admin_user:
        audit_logs = audit_logs.filter(user=user)

    items = []
    for log in audit_logs[:24]:
        title, icon, color, route_name = type_config.get(
            log.action_type,
            ('Системное событие', 'fa-circle-info', 'cyan', 'audit_log'),
        )
        actor = ''
        if log.user_id:
            actor = log.user.get_full_name() or log.user.username
        items.append({
            'key': f'audit:{log.pk}',
            'title': title,
            'message': log.description,
            'actor': actor,
            'icon': icon,
            'color': color,
            'url': reverse(route_name),
            'created_at': log.created_at,
        })

    if user.is_admin_user:
        low_stock = Product.objects.filter(
            is_active=True,
            stock_qty__lte=F('min_stock_alert'),
        ).order_by('stock_qty', 'name')[:8]
        for product in low_stock:
            updated_timestamp = int(product.updated_at.timestamp())
            quantity = format(product.stock_qty.normalize(), 'f')
            items.append({
                'key': f'stock:{product.pk}:{updated_timestamp}',
                'title': 'Заканчивается товар',
                'message': f'{product.name}: осталось {quantity} {product.unit}',
                'actor': 'Склад',
                'icon': 'fa-triangle-exclamation',
                'color': 'orange',
                'url': reverse('product_list') + '?stock=low',
                'created_at': product.updated_at,
            })

    dismissed = set(state.dismissed_keys or [])
    visible = []
    for item in items:
        if item['key'] in dismissed:
            continue
        if state.cleared_before and item['created_at'] <= state.cleared_before:
            continue
        item['is_read'] = bool(state.read_before and item['created_at'] <= state.read_before)
        local_time = timezone.localtime(item['created_at'])
        item['time'] = local_time.strftime('%d.%m, %H:%M')
        item['created_at_iso'] = local_time.isoformat()
        visible.append(item)

    visible.sort(key=lambda item: item['created_at'], reverse=True)
    return visible[:30]


@login_required
@require_GET
def notifications_api(request):
    state, _ = NotificationState.objects.get_or_create(user=request.user)
    items = _notification_feed(request.user, state)
    return JsonResponse({
        'items': [{key: value for key, value in item.items() if key != 'created_at'} for item in items],
        'unread_count': sum(not item['is_read'] for item in items),
        'total_count': len(items),
    })


@login_required
@require_POST
def notifications_action_api(request):
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Некорректный запрос'}, status=400)

    action = payload.get('action')
    state, _ = NotificationState.objects.get_or_create(user=request.user)
    now = timezone.now()

    if action == 'read_all':
        state.read_before = now
        state.save(update_fields=['read_before', 'updated_at'])
    elif action == 'clear_all':
        state.cleared_before = now
        state.dismissed_keys = []
        state.save(update_fields=['cleared_before', 'dismissed_keys', 'updated_at'])
    elif action == 'dismiss':
        notification_key = str(payload.get('key', ''))[:128]
        if not notification_key:
            return JsonResponse({'error': 'Уведомление не найдено'}, status=400)
        dismissed = list(state.dismissed_keys or [])
        if notification_key not in dismissed:
            dismissed.append(notification_key)
            state.dismissed_keys = dismissed[-200:]
            state.save(update_fields=['dismissed_keys', 'updated_at'])
    else:
        return JsonResponse({'error': 'Неизвестное действие'}, status=400)

    items = _notification_feed(request.user, state)
    return JsonResponse({
        'success': True,
        'unread_count': sum(not item['is_read'] for item in items),
        'total_count': len(items),
    })

@login_required
def _legacy_dashboard_view(request):
    # ADMIN-ONLY ACCESS TO DASHBOARD
    if not request.user.is_admin_user:
        messages.error(request, 'Доступ к главному дашборду аналитики ограничен. Раздел только для Администратора.')
        return redirect('pos')

    today = timezone.localdate()
    period = request.GET.get('period', 'day')
    if period not in {'day', 'week', 'month'}:
        period = 'day'

    # The selected period drives every dashboard figure, rather than merely
    # changing the highlighted button in the interface.
    if period == 'week':
        period_start = today - datetime.timedelta(days=6)
        period_label = 'за неделю'
        period_title = 'Неделя'
    elif period == 'month':
        period_start = today.replace(day=1)
        period_label = 'за месяц'
        period_title = 'Месяц'
    else:
        period_start = today
        period_label = 'сегодня'
        period_title = 'Сегодня'
    period_end = today
    first_day_of_month = today.replace(day=1)

    completed_sales = SaleOrder.objects.filter(status=SaleOrder.Status.COMPLETED)

    # Selected-period metrics.  The historical ``today_*`` context names are
    # retained because the mobile dashboard uses them for the default day.
    today_sales = completed_sales.filter(created_at__date=today)
    period_sales = completed_sales.filter(created_at__date__gte=period_start, created_at__date__lte=period_end)
    today_revenue = period_sales.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    today_count = period_sales.count()
    today_avg_check = (today_revenue / today_count) if today_count > 0 else Decimal('0.00')

    # Month's metrics
    month_sales = completed_sales.filter(created_at__date__gte=first_day_of_month)
    month_revenue = month_sales.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    month_count = month_sales.count()

    # Calculate Gross Profit
    today_items = SaleOrderItem.objects.filter(order__in=period_sales)
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

    # Compact cashier ranking for the selected dashboard period.
    cashier_day_rows = period_sales.exclude(cashier=None).values('cashier_id').annotate(
        revenue=Sum('total_amount'),
        checks=Count('id'),
    ).order_by('-revenue')[:3]
    cashier_day_ids = [row['cashier_id'] for row in cashier_day_rows]
    cashier_day_users = User.objects.in_bulk(cashier_day_ids)
    cashier_day_stats = [
        {
            'user': cashier_day_users.get(row['cashier_id']),
            'revenue': row['revenue'] or Decimal('0.00'),
            'checks': row['checks'],
        }
        for row in cashier_day_rows
        if cashier_day_users.get(row['cashier_id'])
    ]

    # Payment breakdown
    payment_stats = period_sales.values('payment_method').annotate(
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

    # Compare the selected range with the immediately preceding range of the
    # same length (yesterday / previous 7 days / previous month-to-date).
    period_days = (period_end - period_start).days + 1
    previous_end = period_start - datetime.timedelta(days=1)
    previous_start = previous_end - datetime.timedelta(days=period_days - 1)
    previous_sales = completed_sales.filter(created_at__date__gte=previous_start, created_at__date__lte=previous_end)
    yesterday_rev = float(previous_sales.aggregate(total_rev=Sum('total_amount'))['total_rev'] or 0)
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
    recent_orders = SaleOrder.objects.select_related('cashier').filter(
        created_at__date__gte=period_start, created_at__date__lte=period_end
    )[:10]
    dashboard_categories = Category.objects.all()[:8]
    top_products = SaleOrderItem.objects.filter(
        order__in=period_sales
    ).values(
        'product__name', 'product__category__icon'
    ).annotate(
        total_qty=Sum('quantity'), total_sum=Sum('total_amount')
    ).order_by('-total_sum')[:8]
    profit_margin = int(round((today_profit / today_revenue) * 100)) if today_revenue else 0

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
        'cashier_day_stats': cashier_day_stats,
        'recent_orders': recent_orders,
        'dashboard_categories': dashboard_categories,
        'top_products': top_products,
        'profit_margin': profit_margin,
        'dashboard_period': period,
        'period_label': period_label,
        'period_title': period_title,
        'period_start': period_start,
        'period_end': period_end,
    })


@login_required
def dashboard_view(request):
    if not request.user.is_admin_user:
        messages.error(request, 'Доступ к аналитике ограничен. Раздел только для Администратора.')
        return redirect('pos')

    selected = _resolve_period(request)
    report = _analytics_report(selected['start'], selected['end'])
    period_days = (selected['end'] - selected['start']).days + 1
    previous_end = selected['start'] - datetime.timedelta(days=1)
    previous_start = previous_end - datetime.timedelta(days=period_days - 1)
    previous = _sales_summary(previous_start, previous_end)
    previous_net = previous['net_revenue']
    current_net = report['net_revenue']
    if previous_net:
        growth_percent = (current_net - previous_net) / abs(previous_net) * 100
    elif current_net:
        growth_percent = Decimal('100.00')
    else:
        growth_percent = ZERO

    # Compatibility keys keep the existing mobile dashboard stable while the
    # richer desktop dashboard consumes the new report object.
    payment_by_code = {row['payment_method']: row for row in report['payment_rows']}
    qr_sum = payment_by_code.get(SaleOrder.PaymentMethod.TRANSFER, {}).get('amount', ZERO)
    cash_sum = payment_by_code.get(SaleOrder.PaymentMethod.CASH, {}).get('amount', ZERO)
    card_sum = payment_by_code.get(SaleOrder.PaymentMethod.CARD, {}).get('amount', ZERO)
    mixed_sum = payment_by_code.get(SaleOrder.PaymentMethod.MIXED, {}).get('amount', ZERO)
    payment_total = report['gross_revenue']

    drilldown_period = {
        'date_from': selected['start'].isoformat(),
        'date_to': selected['end'].isoformat(),
    }

    def orders_drilldown(**filters):
        params = {**drilldown_period, **filters}
        return f"{reverse('sales_orders_list')}?{urlencode(params)}"

    for row in report['payment_rows']:
        row['drilldown_url'] = orders_drilldown(payment_method=row['payment_method'])
    for row in report['top_products']:
        row['drilldown_url'] = orders_drilldown(q=row['product__name'] or '')
    for row in report['cashier_rows']:
        row['drilldown_url'] = (
            orders_drilldown(cashier_id=row['cashier_id'])
            if row['cashier_id'] else orders_drilldown()
        )

    context = {
        'report': report,
        'today_revenue': report['net_revenue'],
        'today_profit': report['gross_profit'],
        'today_count': report['check_count'],
        'today_avg_check': report['average_check'],
        'low_stock_count': report['inventory']['attention_count'],
        'cashier_stats': report['cashier_rows'],
        'cashier_day_stats': report['cashier_rows'][:3],
        'recent_orders': report['recent_orders'],
        'dashboard_categories': Category.objects.all()[:8],
        'top_products': report['top_products'],
        'profit_margin': report['margin'],
        'margin_available': report['margin_available'],
        'growth_percent': growth_percent,
        'comparisons': {
            'net_revenue': _period_comparison(report['net_revenue'], previous['net_revenue']),
            'gross_profit': _period_comparison(report['gross_profit'], previous['gross_profit']),
            'average_check': _period_comparison(report['average_check'], previous['average_check']),
            'refund_amount': _period_comparison(report['refund_amount'], previous['refund_amount'], lower_is_better=True),
            'discounts': _period_comparison(report['discounts'], previous['discounts'], lower_is_better=True),
        },
        'target_percent': 0,
        'yesterday_revenue': previous_net,
        'payment_breakdown': {
            'qr_sum': qr_sum,
            'cash_sum': cash_sum,
            'card_sum': card_sum + mixed_sum,
            'qr_pct': int(qr_sum / payment_total * 100) if payment_total else 0,
            'cash_pct': int(cash_sum / payment_total * 100) if payment_total else 0,
            'card_pct': int((card_sum + mixed_sum) / payment_total * 100) if payment_total else 0,
            'total_sum': payment_total,
        },
        'dashboard_period': selected['period'],
        'period_label': selected['label'],
        'period_title': selected['title'],
        'period_start': selected['start'],
        'period_end': selected['end'],
        'drilldown': {
            'orders': orders_drilldown(),
            'refunds': orders_drilldown(status=SaleOrder.Status.REFUNDED, event='refund'),
            'discounts': orders_drilldown(discounted='1'),
        },
    }
    return render(request, 'analytics/dashboard.html', context)


@login_required
@require_POST
def ai_insight_api(request):
    """Return a read-only natural-language explanation of the selected report."""
    if not request.user.is_admin_user:
        return JsonResponse({'error': 'AI-аналитика доступна только Администратору.'}, status=403)
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Некорректный запрос.'}, status=400)

    selected = _resolve_period(request)
    question = str(payload.get('question', '')).strip()[:800]
    report = _analytics_report(selected['start'], selected['end'])
    try:
        from analytics.ai import AIUnavailable, generate_insight
        answer = generate_insight(report, selected['start'], selected['end'], question)
    except AIUnavailable as exc:
        return JsonResponse({'error': str(exc)}, status=503)
    return JsonResponse({
        'answer': answer,
        'period': {'from': selected['start'].isoformat(), 'to': selected['end'].isoformat()},
    })


@login_required
@require_GET
def analytics_export_csv(request):
    if not request.user.is_admin_user:
        return HttpResponse('Доступ запрещён', status=403)

    selected = _resolve_period(request)
    report = _analytics_report(selected['start'], selected['end'])
    filename = f"dacar-analytics-{selected['start']:%Y-%m-%d}-{selected['end']:%Y-%m-%d}.csv"
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')

    def section(title, headers, rows):
        writer.writerow([])
        writer.writerow([title])
        writer.writerow(headers)
        writer.writerows(rows)

    writer.writerow(['DACAR Market — управленческий отчёт'])
    writer.writerow(['Период', selected['start'].strftime('%d.%m.%Y'), selected['end'].strftime('%d.%m.%Y')])
    writer.writerow(['Сформирован', timezone.localtime().strftime('%d.%m.%Y %H:%M')])
    section('Основные показатели', ['Показатель', 'Значение'], [
        ['Продаж / чеков', report['check_count']],
        ['Продано единиц', report['units_sold']],
        ['Валовая выручка, ₸', report['gross_revenue']],
        ['Возвратных операций', report['refund_count']],
        ['Сумма возвратов, ₸', report['refund_amount']],
        ['Возвратов по чекам выбранного периода', report['cohort_refund_count']],
        ['Доля возвращённых чеков выбранного периода, %', report['refund_rate'].quantize(Decimal('0.01'))],
        ['Возвратов по более ранним чекам', report['prior_period_refund_count']],
        ['Сумма возвратов по более ранним чекам, ₸', report['prior_period_refund_amount']],
        ['Чистая выручка, ₸', report['net_revenue']],
        ['Скидки, ₸', report['discounts']],
        ['Себестоимость реализованного, ₸', report['net_cost']],
        ['Валовая прибыль, ₸', report['gross_profit']],
        ['Валовая маржа, %', report['margin'].quantize(Decimal('0.01')) if report['margin_available'] else 'Не рассчитывается'],
        ['Средний чек, ₸', report['average_check']],
    ])
    inv = report['inventory']
    section('Склад на текущий момент', ['Показатель', 'Значение'], [
        ['Активных товаров', inv['active_count']],
        ['Нет в наличии', inv['out_of_stock_count']],
        ['Мало на складе', inv['low_stock_count']],
        ['Стоимость склада по закупке, ₸', inv['stock_cost']],
        ['Стоимость склада по рознице, ₸', inv['stock_retail']],
        ['Потенциальная валовая прибыль, ₸', inv['potential_profit']],
        ['Без закупочной цены', inv['without_cost_count']],
        ['Без розничной цены', inv['without_price_count']],
        ['Без штрихкода', inv['without_barcode_count']],
        ['Без категории', inv['without_category_count']],
    ])
    section('Способы оплаты', ['Способ', 'Чеков', 'Сумма, ₸', 'Доля, %'], [
        [row['name'], row['checks'], row['amount'], row['share'].quantize(Decimal('0.01'))]
        for row in report['payment_rows']
    ])
    section('Товары', ['Товар', 'Количество', 'Выручка, ₸', 'Себестоимость, ₸', 'Прибыль, ₸'], [
        [row['product__name'] or 'Удалённый товар', row['total_qty'], row['total_sum'], row['total_cost'], row['profit']]
        for row in report['top_products']
    ])
    section('Категории', ['Категория', 'Количество', 'Выручка, ₸', 'Прибыль, ₸'], [
        [row['name'], row['total_qty'], row['revenue'], row['profit']] for row in report['category_rows']
    ])
    section('Кассиры', ['Кассир', 'Чеков', 'Выручка, ₸', 'Средний чек, ₸', 'Прибыль, ₸'], [
        [row['name'], row['checks'], row['revenue'], row['average_check'], row['profit']]
        for row in report['cashier_rows']
    ])
    section('Чеки периода', ['Номер', 'Дата', 'Кассир', 'Оплата', 'Статус', 'Сумма, ₸', 'Скидка, ₸'], [
        [
            order.order_number,
            timezone.localtime(order.created_at).strftime('%d.%m.%Y %H:%M'),
            (order.cashier.get_full_name() or order.cashier.username) if order.cashier else 'Удалённый кассир',
            order.get_payment_method_display(),
            order.get_status_display(),
            order.total_amount,
            order.discount_amount,
        ] for order in report['sales'].select_related('cashier').order_by('-created_at')
    ])
    return response


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
    user_filter = request.GET.get('user_id')
    date_filter = request.GET.get('date', '').strip()

    logs = AuditLog.objects.select_related('user').all()

    if search:
        logs = logs.filter(
            Q(description__icontains=search) |
            Q(user__username__icontains=search) |
            Q(user__first_name__icontains=search)
        )
    if action_type:
        logs = logs.filter(action_type=action_type)
    if user_filter:
        logs = logs.filter(user_id=user_filter)

    if date_filter:
        try:
            selected_date = datetime.datetime.strptime(date_filter, '%Y-%m-%d').date()
            logs = logs.filter(created_at__date=selected_date)
        except ValueError:
            date_filter = ''

    from django.core.paginator import Paginator

    page_number = request.GET.get('page', 1)
    paginator = Paginator(logs, 20)
    page_obj = paginator.get_page(page_number)

    return render(request, 'analytics/audit_log.html', {
        'page_obj': page_obj,
        'logs': page_obj.object_list,
        'search': search,
        'action_type': action_type,
        'user_filter': user_filter,
        'date_filter': date_filter,
        'action_types': AuditLog.ActionType.choices,
        'audit_users': User.objects.filter(auditlog__isnull=False).distinct().order_by('first_name', 'username'),
    })


@login_required
def _legacy_analytics_chart_data_api(request):
    if not request.user.is_admin_user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    today = timezone.localdate()
    # The mobile dashboard calls this endpoint without a period and keeps its
    # familiar seven-day chart. The desktop dashboard explicitly selects one.
    period = request.GET.get('period', 'week')
    if period not in {'day', 'week', 'month'}:
        period = 'week'
    completed_sales = SaleOrder.objects.filter(status=SaleOrder.Status.COMPLETED)

    # 1. Revenue and gross profit over the requested real period.
    dates_label = []
    revenues = []
    profits = []

    if period == 'day':
        chart_dates = [today]
    elif period == 'month':
        first_day = today.replace(day=1)
        chart_dates = [first_day + datetime.timedelta(days=i) for i in range((today - first_day).days + 1)]
    else:
        chart_dates = [today - datetime.timedelta(days=i) for i in range(6, -1, -1)]

    if period == 'day':
        hourly_revenue = [0.0] * 24
        hourly_profit = [0.0] * 24
        day_orders = completed_sales.filter(created_at__date=today)
        for order in day_orders:
            hour = timezone.localtime(order.created_at).hour
            hourly_revenue[hour] += float(order.total_amount)
        for item in SaleOrderItem.objects.filter(order__in=day_orders):
            hour = timezone.localtime(item.order.created_at).hour
            hourly_profit[hour] -= float(item.quantity * item.purchase_price_snapshot)
        for hour in range(24):
            dates_label.append(f'{hour:02d}:00')
            revenues.append(hourly_revenue[hour])
            profits.append(hourly_revenue[hour] + hourly_profit[hour])
    else:
        for target_date in chart_dates:
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
def analytics_chart_data_api(request):
    if not request.user.is_admin_user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    selected = _resolve_period(request, default='week')
    start, end = selected['start'], selected['end']
    labels, revenues, profits = [], [], []

    if selected['period'] == 'day' and start == end:
        labels = [f'{hour:02d}:00' for hour in range(24)]
        revenues = [0.0] * 24
        profits = [0.0] * 24
        summary = _sales_summary(start, end)
        for order in summary['sales'].prefetch_related('items'):
            hour = timezone.localtime(order.created_at).hour
            order_cost = sum(item.quantity * item.purchase_price_snapshot for item in order.items.all())
            revenues[hour] += float(order.total_amount)
            profits[hour] += float(order.total_amount - order_cost)
        for order in summary['refunds'].prefetch_related('items'):
            event_time = order.refunded_at or order.created_at
            hour = timezone.localtime(event_time).hour
            order_cost = sum(item.quantity * item.purchase_price_snapshot for item in order.items.all())
            revenues[hour] -= float(order.total_amount)
            profits[hour] -= float(order.total_amount - order_cost)
    else:
        ranges = []
        if selected['period'] == 'year' or (end - start).days > 45:
            cursor = start.replace(day=1)
            while cursor <= end:
                if cursor.month == 12:
                    next_month = cursor.replace(year=cursor.year + 1, month=1)
                else:
                    next_month = cursor.replace(month=cursor.month + 1)
                range_start = max(cursor, start)
                range_end = min(next_month - datetime.timedelta(days=1), end)
                ranges.append((range_start, range_end, cursor.strftime('%m.%Y')))
                cursor = next_month
        else:
            cursor = start
            while cursor <= end:
                ranges.append((cursor, cursor, cursor.strftime('%d.%m')))
                cursor += datetime.timedelta(days=1)
        for range_start, range_end, label in ranges:
            point = _sales_summary(range_start, range_end)
            labels.append(label)
            revenues.append(float(point['net_revenue']))
            profits.append(float(point['gross_profit']))

    report = _analytics_report(start, end)
    return JsonResponse({
        'sales_trend': {'labels': labels, 'revenues': revenues, 'profits': profits},
        'payment_methods': {
            'labels': [row['name'] for row in report['payment_rows']],
            'values': [float(row['amount']) for row in report['payment_rows']],
        },
        'top_products': {
            'labels': [row['product__name'] or 'Удалённый товар' for row in report['top_products'][:5]],
            'values': [float(row['total_qty']) for row in report['top_products'][:5]],
        },
    })


@login_required
def _legacy_live_kpi_api(request):
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


@login_required
def live_kpi_api(request):
    """Live mobile figures use the same accounting rules as the full report."""
    if not request.user.is_admin_user:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    from django.core.cache import cache

    today = timezone.localdate()
    cache_key = f'dacar_live_kpi_v2_{today.isoformat()}'
    cached_data = cache.get(cache_key)
    if cached_data:
        return JsonResponse(cached_data)

    yesterday = today - datetime.timedelta(days=1)
    report = _analytics_report(today, today)
    previous = _sales_summary(yesterday, yesterday)
    today_rev = report['net_revenue']
    previous_rev = previous['net_revenue']
    if previous_rev:
        growth = (today_rev - previous_rev) / abs(previous_rev) * 100
    elif today_rev:
        growth = Decimal('100')
    else:
        growth = ZERO

    hourly_sales = [0.0] * 24
    for order in report['sales']:
        hourly_sales[timezone.localtime(order.created_at).hour] += float(order.total_amount)
    for order in report['refunds']:
        event_time = order.refunded_at or order.created_at
        hourly_sales[timezone.localtime(event_time).hour] -= float(order.total_amount)

    payments = {row['payment_method']: row['amount'] for row in report['payment_rows']}
    payment_total = report['gross_revenue']
    qr_sum = payments.get(SaleOrder.PaymentMethod.TRANSFER, ZERO)
    cash_sum = payments.get(SaleOrder.PaymentMethod.CASH, ZERO)
    card_sum = payments.get(SaleOrder.PaymentMethod.CARD, ZERO) + payments.get(SaleOrder.PaymentMethod.MIXED, ZERO)
    last_order = report['recent_orders'][0] if report['recent_orders'] else None

    response_payload = {
        'revenue_today': float(today_rev),
        'revenue_yesterday': float(previous_rev),
        'growth_percent': round(float(growth), 1),
        'target_percent': 0,
        'profit_today': float(report['gross_profit']),
        'margin_today': float(report['margin']) if report['margin_available'] else None,
        'orders_count_today': report['check_count'],
        'refunds_count_today': report['refund_count'],
        'refund_amount_today': float(report['refund_amount']),
        'avg_check_today': round(float(report['average_check']), 2),
        'low_stock_count': report['inventory']['attention_count'],
        'payment_breakdown': {
            'qr_sum': float(qr_sum),
            'qr_pct': int(qr_sum / payment_total * 100) if payment_total else 0,
            'cash_sum': float(cash_sum),
            'cash_pct': int(cash_sum / payment_total * 100) if payment_total else 0,
            'card_sum': float(card_sum),
            'card_pct': int(card_sum / payment_total * 100) if payment_total else 0,
        },
        'last_order': {
            'id': last_order.id,
            'order_number': last_order.order_number,
            'cashier_name': (last_order.cashier.get_full_name() or last_order.cashier.username) if last_order.cashier else 'Удалённый кассир',
            'total_amount': float(last_order.total_amount),
            'payment_method_display': last_order.get_payment_method_display(),
            'status': last_order.status,
            'time': timezone.localtime(last_order.created_at).strftime('%H:%M:%S'),
        } if last_order else None,
        'hourly_sales': hourly_sales,
        'server_time': timezone.localtime().strftime('%H:%M:%S'),
    }
    cache.set(cache_key, response_payload, timeout=10)
    return JsonResponse(response_payload)
