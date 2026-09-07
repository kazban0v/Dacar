from django.shortcuts import redirect, get_object_or_404
from config.rendering import render, is_mobile_request
from django.contrib.auth import login, logout, authenticate, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.http import JsonResponse
from django.db.models import Count, Q, Sum
from django.utils import timezone
from datetime import timedelta
from users.models import User
from sales.models import SaleOrder


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
        u_pass = request.POST.get('password', '').strip()

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
        role = request.POST.get('role', User.Role.CASHIER)

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
        'roles': User.Role.choices
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

            if User.objects.filter(username=username).exists():
                messages.error(request, 'Пользователь с таким логином уже существует.')
            else:
                User.objects.create_user(
                    username=username,
                    password=password,
                    first_name=first_name,
                    last_name=last_name,
                    phone=phone,
                    role=role
                )
                messages.success(request, f'Пользователь {username} успешно создан!')
                return redirect(redirect_url)
                
        elif action == 'toggle_status':
            target_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, id=target_id)
            if target_user.username == 'beybit':
                messages.error(request, 'Нельзя изменить статус главного администратора.')
                return redirect(redirect_url)
            if target_user != request.user:
                target_user.is_active = not target_user.is_active
                target_user.save()
                messages.info(request, f'Статус пользователя {target_user.username} изменен.')
            return redirect(redirect_url)

        elif action == 'set_role':
            target_id = request.POST.get('user_id')
            role = request.POST.get('role')
            target_user = get_object_or_404(User, id=target_id)
            if target_user.username == 'beybit' or target_user == request.user:
                messages.error(request, 'Нельзя изменить роль этого аккаунта.')
                return redirect(redirect_url)
            if role not in dict(User.Role.choices):
                messages.error(request, 'Выберите корректную роль.')
                return redirect(redirect_url)
            target_user.role = role
            target_user.save(update_fields=['role'])
            messages.success(request, f'Роль сотрудника {target_user.username} обновлена.')
            return redirect(redirect_url)

        elif action == 'delete':
            target_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, id=target_id)
            if target_user.username == 'beybit':
                messages.error(request, 'Нельзя удалить главного администратора!')
                return redirect(redirect_url)
            if target_user == request.user:
                messages.error(request, 'Вы не можете удалить свой собственный аккаунт!')
            else:
                uname = target_user.username
                try:
                    target_user.delete()
                    from analytics.models import AuditLog
                    AuditLog.log(
                        request,
                        AuditLog.ActionType.USER_ACTION,
                        f"Администратор {request.user} полностью удалил аккаунт сотрудника '{uname}'"
                    )
                    messages.success(request, f'Пользователь "{uname}" успешно удален из системы.')
                except Exception:
                    # User has financial/sales history! Deactivate instead of crashing.
                    target_user.is_active = False
                    target_user.save()
                    from analytics.models import AuditLog
                    AuditLog.log(
                        request,
                        AuditLog.ActionType.USER_ACTION,
                        f"Администратор {request.user} деактивировал профиль '{uname}' (удаление невозможно из-за наличия истории проведенных чеков)"
                    )
                    messages.warning(
                        request,
                        f'Пользователь "{uname}" проводил чеки в кассе. Для сохранения отчетов продаж аккаунт деактивирован и заблокирован.'
                    )
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
        staff_cards.append({
            'user': staff_user,
            'sparkline_points': sparkline_points(values),
            'has_sales': any(values),
            'trend_direction': trend_direction,
            'month_revenue': month.get('total', 0),
            'month_orders': month.get('count', 0),
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
