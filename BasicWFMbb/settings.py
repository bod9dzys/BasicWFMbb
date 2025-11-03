"""
Django settings for BasicWFMbb project.
"""

from pathlib import Path
from dotenv import load_dotenv
import dj_database_url
import os

# 1. Завантажуємо .env (це у вас працює)
load_dotenv()

# 2. Визначаємо BASE_DIR (це у вас працює)
BASE_DIR = Path(__file__).resolve().parent.parent


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


# ▼▼▼ КРИТИЧНИЙ БЛОК: МЕДІА-ФАЙЛИ (МАЄ БУТИ ДО INSTALLED_APPS) ▼▼▼
GS_BUCKET_NAME = os.environ.get('GS_BUCKET_NAME')
GCS_CREDENTIALS_FILE = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
GCS_KEYFILE_PATH = None

if GS_BUCKET_NAME and GCS_CREDENTIALS_FILE:
    if not os.path.isabs(GCS_CREDENTIALS_FILE):
        # Ваш лог показує, що файл лежить у папці /BasicWFMbb/, поруч з settings.py
        GCS_KEYFILE_PATH = BASE_DIR / 'BasicWFMbb' / GCS_CREDENTIALS_FILE
    else:
        GCS_KEYFILE_PATH = Path(GCS_CREDENTIALS_FILE) # Для абсолютних шляхів

    if GCS_KEYFILE_PATH and GCS_KEYFILE_PATH.exists():
        print(f"!!! GCS INIT: Ключ знайдено: {GCS_KEYFILE_PATH}")
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(GCS_KEYFILE_PATH)

        # Встановлюємо сховище GCS
        DEFAULT_FILE_STORAGE = 'storages.backends.gcloud.GoogleCloudStorage'
        GS_DEFAULT_ACL = 'publicRead'
        MEDIA_URL = f'https://storage.googleapis.com/{GS_BUCKET_NAME}/media/'
        MEDIA_ROOT = 'media/'

        # Новий діагностичний print
        print(f"!!! GCS INIT: DEFAULT_FILE_STORAGE ВСТАНОВЛЕНО в 'GoogleCloudStorage'")
    else:
        print(f"!!! GCS INIT: ПОМИЛКА! Ключ не знайдено за шляхом: {GCS_KEYFILE_PATH}")
        DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
        MEDIA_URL = 'media/'
        MEDIA_ROOT = BASE_DIR / 'media'
else:
    print("!!! GCS INIT: GCS не налаштовано, використовую локальне сховище 'media/'.")
    DEFAULT_FILE_STORAGE = 'django.core.files.storage.FileSystemStorage'
    MEDIA_URL = 'media/'
    MEDIA_ROOT = BASE_DIR / 'media'
# -------------------------------
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲


# Application definition
# Django прочитає 'core' і завантажить models.py ТУТ,
# тому DEFAULT_FILE_STORAGE вже має бути визначено.
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'core', # <--- Цей рядок завантажує core/models.py

    'crispy_forms',
    'crispy_bootstrap5',
    'django_filters',
    'import_export',
    'simple_history',

    'storages',
]

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
if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=600,
            ssl_require=True
        )
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
# -------------------

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