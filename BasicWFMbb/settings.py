"""
Django settings for BasicWFMbb project.
"""

from pathlib import Path
from dotenv import load_dotenv
import dj_database_url
import os
from google.oauth2 import service_account

# 1. Завантажуємо .env (це у вас працює)
load_dotenv()

# 2. Визначаємо BASE_DIR (це у вас працює)
BASE_DIR = Path(__file__).resolve().parent.parent
# 2.1. Визначаємо папку налаштувань
SETTINGS_DIR = Path(__file__).resolve().parent

# 3. Базові налаштування
SECRET_KEY = os.environ.get(
    'SECRET_KEY',
    'django-insecure-f=%z%o-_z(05*+im8g9%iiuhs_o+wylhx)5lnnf708zs$+inq2'
)
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# --- Налаштування хостів (ALLOWED_HOSTS) ---
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']
RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)
DJANGO_ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS')
if DJANGO_ALLOWED_HOSTS:
    ALLOWED_HOSTS.extend(DJANGO_ALLOWED_HOSTS.split(','))
# ------------------------------------


# --- Статика (CSS, JS) ---
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
# -------------------------



# Application definition
# Django прочитає 'core' і завантажить models.py ТУТ,

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'storages',
    'core', # <--- Цей рядок завантажує core/models.py

    'crispy_forms',
    'crispy_bootstrap5',
    'django_filters',
    'import_export',
    'simple_history',


]

GS_BUCKET_NAME = os.getenv("GS_BUCKET_NAME")
GOOGLE_APPLICATION_CREDENTIALS_PATH = os.path.join(
    BASE_DIR, os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
)

GS_CREDENTIALS = service_account.Credentials.from_service_account_file(
    GOOGLE_APPLICATION_CREDENTIALS_PATH
)

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.gcloud.GoogleCloudStorage",
        "OPTIONS": {
            "bucket_name": GS_BUCKET_NAME,
            "credentials": GS_CREDENTIALS,
        },
    },
    # якщо статичні файли локально — лишай так
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}

MEDIA_URL = f"https://storage.googleapis.com/{GS_BUCKET_NAME}/"

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'core.middleware.CurrentUserMiddleware',
    'core.middleware.LoginRequiredMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'simple_history.middleware.HistoryRequestMiddleware',
]

ROOT_URLCONF = 'BasicWFMbb.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
            'django.template.context_processors.debug',
            'django.template.context_processors.request',
            'django.contrib.auth.context_processors.auth',
            'django.contrib.messages.context_processors.messages',
            'core.context_processors.sick_leave_notifications',
        ],
    },
},
]

WSGI_APPLICATION = 'BasicWFMbb.wsgi.application'

# --- База даних ---
DATABASE_URL = os.environ.get('DATABASE_URL')
DATABASES = {
    'default': dj_database_url.config(
        default=DATABASE_URL,
        conn_max_age=600,
        ssl_require=True
    )
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',},
]

# Internationalization
LANGUAGE_CODE = 'uk'
TIME_ZONE = 'Europe/Kyiv'
USE_I18N = True
USE_TZ = True

# --- Налаштування входу ---
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/schedule/"
LOGOUT_REDIRECT_URL = "/accounts/login/"
LOGIN_EXEMPT_URLS = [
    "/accounts/login/",
    "/accounts/signup/",
    "/admin/login/",
    "/admin/logout/",
]
LOGIN_EXEMPT_URL_NAMES = [
    "login",
    "signup",
    "logout",
]
LOGIN_EXEMPT_PREFIXES = [
    "/static/",
]
# --------------------------

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'