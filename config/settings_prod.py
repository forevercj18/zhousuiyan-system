"""
Production settings.
Use with: DJANGO_SETTINGS_MODULE=config.settings_prod
"""

from django.core.exceptions import ImproperlyConfigured

from .settings_common import *  # noqa: F401,F403


DEBUG = False

if SECRET_KEY == "django-insecure-zhousuiyan-baby-party-props-rental-system-2024":
    raise ImproperlyConfigured("SECRET_KEY must be provided in production environment.")

if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("ALLOWED_HOSTS must be provided in production environment.")

if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    raise ImproperlyConfigured("Production environment must not use SQLite. Set DB_ENGINE=postgres.")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", True)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", True)
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = env_bool("CSRF_COOKIE_HTTPONLY", False)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_REFERRER_POLICY = os.getenv("SECURE_REFERRER_POLICY", "same-origin")
