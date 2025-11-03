from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.urls import resolve, Resolver404
import threading


_local = threading.local()


def get_current_user():
    return getattr(_local, "user", None)


def get_current_request():
    return getattr(_local, "request", None)


class CurrentUserMiddleware:
    """
    Store current request and user in thread-local storage so signals
    can access who performed a change without passing request around.
    Place after AuthenticationMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.request = request
        _local.user = getattr(request, "user", None)
        try:
            response = self.get_response(request)
        finally:
            # Clean up to avoid leaking references in long-running processes
            _local.request = None
            _local.user = None
        return response


class LoginRequiredMiddleware:
    """
    Redirect anonymous users to the login page unless the requested path
    is explicitly exempted.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.exempt_urls = set(getattr(settings, "LOGIN_EXEMPT_URLS", []))
        self.exempt_names = set(getattr(settings, "LOGIN_EXEMPT_URL_NAMES", []))
        self.exempt_prefixes = tuple(getattr(settings, "LOGIN_EXEMPT_PREFIXES", ["/static/"]))

    def __call__(self, request):
        path = request.path_info

        if request.user.is_authenticated:
            return self.get_response(request)

        if path.startswith(self.exempt_prefixes):
            return self.get_response(request)

        if path in self.exempt_urls:
            return self.get_response(request)

        if self.exempt_names:
            try:
                match = resolve(path)
            except Resolver404:
                pass
            else:
                if match.url_name in self.exempt_names or (
                    match.app_name and f"{match.app_name}:{match.url_name}" in self.exempt_names
                ):
                    return self.get_response(request)

        return redirect_to_login(path, settings.LOGIN_URL)
