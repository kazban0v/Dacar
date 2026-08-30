from django.conf import settings
from django.shortcuts import redirect
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden

class RegistrationControlMiddleware:
    """
    Middleware that checks if registration is allowed (ALLOW_REGISTRATION setting).
    If ALLOW_REGISTRATION is False, access to registration views is blocked.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Paths that represent registration
        registration_paths = ['/users/register/', '/api/users/register/']
        
        allow_registration = getattr(settings, 'ALLOW_REGISTRATION', True)

        if not allow_registration and request.path in registration_paths:
            if request.path.startswith('/api/'):
                return JsonResponse({
                    'detail': 'Регистрация новых пользователей отключена администратором.'
                }, status=403)
            
            messages.error(request, 'Регистрация новых пользователей отключена в системе.')
            return redirect('login')

        response = self.get_response(request)
        return response
