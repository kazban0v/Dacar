from django.shortcuts import redirect, get_object_or_404
from config.rendering import render, is_mobile_request
from django.contrib.auth import login, logout, authenticate, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.http import JsonResponse
from django.db.models import Count, Q, Sum, Min, Max
from django.utils import timezone
from datetime import timedelta
from users.models import User
from users.permissions import can_manage_staff
from sales.models import SaleOrder
from analytics.models import AuditLog


def _post_login_redirect(request):
    """Redirect to mobile hub or desktop POS based on request context."""
    if is_mobile_request(request):
        return redirect('/m/')
    return redirect('pos')


def login_view(request):
    if request.user.is_authenticated:
        return _post_login_redirect(request)

    if request.method == 'POST':
        u_name = request.POST.get('username', '').strip()
        u_pass = request.POST.get('password', '')

        user = authenticate(request, username=u_name, password=u_pass)
        if user is not None:
            if not user.is_active:
                messages.error(request, 'Ваш аккаунт деактивирован. Обратитесь к администратору.')
            else:
                login(request, user)
                messages.success(request, f'Добро пожаловать, {user.first_name or user.username}!')
                return _post_login_redirect(request)
        else:
            messages.error(request, 'Неверное имя пользователя или пароль.')

    return render(request, 'users/login.html', {
        'ALLOW_REGISTRATION': getattr(settings, 'ALLOW_REGISTRATION', True)
    })


def logout_view(request):
    mobile = is_mobile_request(request)
    logout(request)
    messages.info(request, 'Вы успешно вышли из системы.')
    if mobile:
        return redirect('/m/users/login/')
    return redirect('login')


def register_view(request):
    allow_reg = getattr(settings, 'ALLOW_REGISTRATION', True)
    if not allow_reg:
        messages.error(request, 'Публичная регистрация отключена в конфигурации системы.')
        return redirect('login')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        password = request.POST.get('password', '').strip()
        phone = request.POST.get('phone', '').strip()
        # Public registration must never accept privileges from the client.
        role = User.Role.CASHIER

        if not username or not password:
            messages.error(request, 'Пожалуйста, заполните логин и пароль.')
        elif User.objects.filter(username=username).exists():
            messages.error(request, 'Пользователь с таким логином уже существует.')
        else:
            user = User.objects.create_user(
                username=username,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                role=role
            )
            login(request, user)
            messages.success(request, 'Профиль успешно зарегистрирован!')
            return _post_login_redirect(request)

    return render(request, 'users/register.html', {
        'roles': [(User.Role.CASHIER, User.Role.CASHIER.label)]
    })


@login_required
def users_list_view(request):
    if not request.user.is_admin_user:
        messages.error(request, 'Доступ ограничен. Только для администраторов.')
        return redirect('pos')

    redirect_url = 'm_users_list' if is_mobile_request(request) else 'users_list'

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create':
            username = request.POST.get('username', '').strip()
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            password = request.POST.get('password', '').strip()
            phone = request.POST.get('phone', '').strip()
            role = request.POST.get('role', User.Role.CASHIER)

            if not username or not password:
                messages.error(request, 'Заполните логин и пароль.')
            elif role not in User.Role.values:
                messages.error(request, 'Выберите корректную роль.')
            elif User.objects.filter(username=username).exists():
                messages.error(request, 'Пользователь с таким логином уже существует.')
            else:
                created_user = User.objects.create_user(
                    username=username,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    phone=phone,
                    role=role
                )
                AuditLog.log(request, AuditLog.ActionType.USER_ACTION,
                    f"Создан сотрудник «{created_user.username}» с ролью «{created_user.get_role_display()}».")
                messages.success(request, f'Пользователь {username} успешно создан!')
                return redirect(redirect_url)
                
        elif action == 'toggle_status':
            target_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, id=target_id)
            if not can_manage_staff(request.user, target_user):
                messages.error(request, 'Этот аккаунт защищён от изменения статуса.')
                return redirect(redirect_url)
            if target_user != request.user:
                target_user.is_active = not target_user.is_active
                target_user.save(update_fields=['is_active'])
                state = 'разблокирован' if target_user.is_active else 'заблокирован'
                AuditLog.log(request, AuditLog.ActionType.USER_ACTION,
                    f"Сотрудник «{target_user.username}» {state}.",
                    metadata={
                        'action': 'toggle_status',
                        'target_user_id': target_user.pk,
                        'target_username': target_user.username,
                        'changes': [{'field': 'is_active', 'label': 'Статус аккаунта',
                                     'old': 'Заблокирован' if target_user.is_active else 'Активен',
                                     'new': 'Активен' if target_user.is_active else 'Заблокирован'}],
                    })
                messages.info(request, f'Сотрудник {target_user.username}: {state}.')
            return redirect(redirect_url)

        elif action == 'set_role':
            target_id = request.POST.get('user_id')
            role = request.POST.get('role')
            target_user = get_object_or_404(User, id=target_id)
            if not can_manage_staff(request.user, target_user):
                messages.error(request, 'Нельзя изменить роль этого аккаунта.')
                return redirect(redirect_url)
            if role not in dict(User.Role.choices):
                messages.error(request, 'Выберите корректную роль.')
                return redirect(redirect_url)
            previous_role = target_user.get_role_display()
            target_user.role = role
            target_user.save(update_fields=['role'])
            AuditLog.log(request, AuditLog.ActionType.USER_ACTION,
                f"Роль сотрудника «{target_user.username}»: {previous_role} → {target_user.get_role_display()}.",
                metadata={
                    'action': 'set_role',
                    'target_user_id': target_user.pk,
                    'target_username': target_user.username,
                    'changes': [{'field': 'role', 'label': 'Роль',
                                 'old': previous_role,
                                 'new': target_user.get_role_display()}],
                })
            messages.success(request, f'Роль сотрудника {target_user.username} обновлена.')
            return redirect(redirect_url)

        elif action == 'delete':
            target_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, id=target_id)
            if not can_manage_staff(request.user, target_user):
                messages.error(request, 'Этот аккаунт защищён от удаления.')
                return redirect(redirect_url)
            if target_user == request.user:
                messages.error(request, 'Вы не можете заблокировать свой собственный аккаунт!')
            else:
                target_user.is_active = False
                target_user.save(update_fields=['is_active'])
                AuditLog.log(request, AuditLog.ActionType.USER_ACTION,
                    f"Сотрудник «{target_user.username}» заблокирован. История сохранена.",
                    metadata={
                        'action': 'block_user',
                        'target_user_id': target_user.pk,
                        'target_username': target_user.username,
                        'changes': [{'field': 'is_active', 'label': 'Статус аккаунта',
                                     'old': 'Активен', 'new': 'Заблокирован'}],
                    })
                messages.info(request, f'Сотрудник «{target_user.username}» заблокирован; история не удалена.')
            return redirect(redirect_url)

    # В существующей базе несколько рабочих аккаунтов были созданы как
    # superuser. Их нельзя скрывать из штата: это те же реальные сотрудники.
    # Исключаем только главный технический аккаунт владельца.
    users = User.objects.exclude(username='beybit').order_by('-date_joined')
    staff_stats = users.aggregate(
        total=Count('id'),
        active=Count('id', filter=Q(is_active=True)),
        admins=Count('id', filter=Q(role=User.Role.ADMIN)),
        cashiers=Count('id', filter=Q(role=User.Role.CASHIER)),
    )

    # Реальные продажи за последние семь дней — источник мини-графика на
    # карточке сотрудника. Возвраты не считаются выручкой.
    today = timezone.localdate()
    trend_start = today - timedelta(days=6)
    completed_sales = SaleOrder.objects.filter(
        status=SaleOrder.Status.COMPLETED,
        cashier_id__in=users.values('id'),
    )
    trend_rows = completed_sales.filter(created_at__date__gte=trend_start).values(
        'cashier_id', 'created_at__date'
    ).annotate(total=Sum('total_amount'))
    trend_by_user = {}
    for row in trend_rows:
        trend_by_user.setdefault(row['cashier_id'], {})[row['created_at__date']] = float(row['total'] or 0)

    month_start = today.replace(day=1)
    month_rows = completed_sales.filter(created_at__date__gte=month_start).values('cashier_id').annotate(
        total=Sum('total_amount'),
        count=Count('id'),
    )
    month_by_user = {row['cashier_id']: row for row in month_rows}

    today_completed_rows = completed_sales.filter(created_at__date=today).values('cashier_id').annotate(
        revenue=Sum('total_amount'), checks=Count('id'), first_sale=Min('created_at'), last_sale=Max('created_at'),
    )
    today_by_user = {row['cashier_id']: row for row in today_completed_rows}
    today_refunds_rows = SaleOrder.objects.filter(
        status=SaleOrder.Status.REFUNDED, refunded_at__date=today,
    ).values('cashier_id').annotate(refunds=Count('id'), refunded_total=Sum('total_amount'))
    refunds_by_user = {row['cashier_id']: row for row in today_refunds_rows}

    last_action_by_user = {}
    for action in AuditLog.objects.filter(user_id__in=users.values('id')).order_by('-created_at').values(
        'user_id', 'description', 'created_at'
    ):
        last_action_by_user.setdefault(action['user_id'], action)

    def sparkline_points(values):
        width, height, inset = 82, 28, 3
        max_value = max(values) or 1
        step = width / max(len(values) - 1, 1)
        return ' '.join(
            f'{index * step:.1f},{height - inset - ((value / max_value) * (height - inset * 2)):.1f}'
            for index, value in enumerate(values)
        )

    staff_cards = []
    for staff_user in users:
        sales_by_day = trend_by_user.get(staff_user.id, {})
        values = [sales_by_day.get(today - timedelta(days=offset), 0) for offset in range(6, -1, -1)]
        # Цвет линии отражает фактическое направление за неделю:
        # последняя точка выше первой — рост, ниже — спад, равна — без изменений.
        trend_direction = (
            'up' if values[-1] > values[0]
            else 'down' if values[-1] < values[0]
            else 'flat'
        )
        month = month_by_user.get(staff_user.id, {})
        today_stats = today_by_user.get(staff_user.id, {})
        refunds = refunds_by_user.get(staff_user.id, {})
        last_action = last_action_by_user.get(staff_user.id)
        staff_cards.append({
            'user': staff_user,
            'can_manage': can_manage_staff(request.user, staff_user),
            'sparkline_points': sparkline_points(values),
            'has_sales': any(values),
            'trend_direction': trend_direction,
            'month_revenue': month.get('total', 0),
            'month_orders': month.get('count', 0),
            'today_revenue': today_stats.get('revenue', 0),
            'today_orders': today_stats.get('checks', 0),
            'today_refunds': refunds.get('refunds', 0),
            'today_refunds_total': refunds.get('refunded_total', 0),
            'last_action': last_action,
        })

    active_staff_cards = [card for card in staff_cards if card['user'].is_active]
    month_leader = max(active_staff_cards, key=lambda card: card['month_revenue'], default=None)

    return render(request, 'users/users_list.html', {
        'users_list': users,
        'staff_cards': staff_cards,
        'staff_stats': staff_stats,
        'month_leader': month_leader,
        'roles': User.Role.choices,
        'ALLOW_REGISTRATION': getattr(settings, 'ALLOW_REGISTRATION', True)
    })
