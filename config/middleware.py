"""
DACAR Mobile Login Redirect Middleware

When @login_required redirects an unauthenticated user to LOGIN_URL,
this middleware intercepts the redirect and rewrites it to the mobile
login path if the original request came from a mobile context (/m/ prefix
or DACAR mobile User-Agent).
"""

from django.conf import settings
from django.shortcuts import redirect


class MobileLoginRedirectMiddleware:
    """
    Intercepts 302 redirects to the desktop login URL and rewrites them
    to /m/users/login/ when the request is from a mobile context.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Only intercept 302 redirects to the desktop login page
        if response.status_code == 302 and not request.user.is_authenticated:
            location = response.get('Location', '')
            login_url = settings.LOGIN_URL
            # Resolve named URL to path if needed
            from django.urls import reverse, NoReverseMatch
            try:
                login_path = reverse(login_url)
            except NoReverseMatch:
                login_path = login_url

            # Check if redirect target is the desktop login page
            if login_path in location:
                # Check if request was from mobile context
                is_mobile = (
                    request.path.startswith('/m/')
                    or 'DACARMobileApp' in request.META.get('HTTP_USER_AGENT', '')
                    or 'DACAR_Android_Native_POS' in request.META.get('HTTP_USER_AGENT', '')
                )
                if is_mobile:
                    # Do NOT preserve next parameter if request was to logout!
                    if 'logout' in request.path:
                        return redirect('/m/users/login/')
                    next_param = request.get_full_path()
                    return redirect(f'/m/users/login/?next={next_param}')

        return response
