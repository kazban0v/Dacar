from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.http import JsonResponse
from users.models import User

def login_view(request):
    if request.user.is_authenticated:
        return redirect('pos')

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
                return redirect('pos')
        else:
            messages.error(request, 'Неверное имя пользователя или пароль.')

    return render(request, 'users/login.html', {
        'ALLOW_REGISTRATION': getattr(settings, 'ALLOW_REGISTRATION', True)
    })


def logout_view(request):
    logout(request)
    messages.info(request, 'Вы успешно вышли из системы.')
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
            return redirect('pos')

    return render(request, 'users/register.html', {
        'roles': User.Role.choices
    })


@login_required
def users_list_view(request):
    if not request.user.is_admin_user:
        messages.error(request, 'Доступ ограничен. Только для администраторов.')
        return redirect('pos')

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
                return redirect('users_list')
                
        elif action == 'toggle_status':
            target_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, id=target_id)
            if target_user != request.user:
                target_user.is_active = not target_user.is_active
                target_user.save()
                messages.info(request, f'Статус пользователя {target_user.username} изменен.')
            return redirect('users_list')

        elif action == 'delete':
            target_id = request.POST.get('user_id')
            target_user = get_object_or_404(User, id=target_id)
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
            return redirect('users_list')

    users = User.objects.all().order_by('-date_joined')
    return render(request, 'users/users_list.html', {
        'users_list': users,
        'roles': User.Role.choices,
        'ALLOW_REGISTRATION': getattr(settings, 'ALLOW_REGISTRATION', True)
    })
