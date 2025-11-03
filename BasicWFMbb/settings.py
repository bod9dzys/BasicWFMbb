"""
Django settings for BasicWFMbb project.
"""

from pathlib import Path
from dotenv import load_dotenv
import dj_database_url
import os

load_dotenv()
print(f"GS_BUCKET_NAME з .env: {os.environ.get('GS_BUCKET_NAME')}")

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    'SECRET_KEY',
    'django-insecure-f=%z%o-_z(05*+im8g9%iiuhs_o+wylhx)5lnnf708zs$+inq2'
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'False') == 'True'


# ▼▼▼ ВИПРАВЛЕНО ALLOWED_HOSTS ▼▼▼
# Спочатку ініціалізуємо базові хости
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']

# Render автоматично надає цю змінну з вашим .onrender.com доменом
RENDER_EXTERNAL_HOSTNAME = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

# Можна також додати власну змінну для додаткових доменів
DJANGO_ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS')
if DJANGO_ALLOWED_HOSTS:
    ALLOWED_HOSTS.extend(DJANGO_ALLOWED_HOSTS.split(','))
# ------------------------------------


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'core',

    'crispy_forms',
    'crispy_bootstrap5',
    'django_filters',
    'import_export',
    'simple_history',

    'storages',
]

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"


# ▼▼▼ ВИПРАВЛЕНО MIDDLEWARE ▼▼▼
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware', # <--- Має бути тут
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
# ------------------------------------


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


# --- Статика (CSS, JS) ---
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
# -------------------------


GS_BUCKET_NAME = os.environ.get('GS_BUCKET_NAME')
GCS_CREDENTIALS_FILE = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')

if GS_BUCKET_NAME and GCS_CREDENTIALS_FILE:
    # Якщо змінна GCS_CREDENTIALS_FILE НЕ є повним шляхом (тобто не містить C: або /),
    # ми припускаємо, що це назва файлу в корені проєкту (BASE_DIR)
    if not os.path.isabs(GCS_CREDENTIALS_FILE):
        GCS_KEYFILE_PATH = BASE_DIR / GCS_CREDENTIALS_FILE
    else:
        GCS_KEYFILE_PATH = GCS_CREDENTIALS_FILE

    # Встановлюємо змінну оточення, яку побачить бібліотека Google
    os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = str(GCS_KEYFILE_PATH)

    # Перевіряємо, чи файл реально існує за цим шляхом
    if not Path(GCS_KEYFILE_PATH).exists():
        print("=" * 50)
        print(f"ПОМИЛКА: Файл ключа GCS не знайдено за шляхом: {GCS_KEYFILE_PATH}")
        print("Переконайтеся, що файл лежить у корені проєкту.")
        print("=" * 50)
        # Якщо файлу немає, примусово вимикаємо GCS
        GS_BUCKET_NAME = None
    else:
        print(f"OK: Ключ GCS знайдено: {GCS_KEYFILE_PATH}")

        # Налаштування для Google Cloud Storage (GCS)
        DEFAULT_FILE_STORAGE = 'storages.backends.gcloud.GoogleCloudStorage'
        GS_DEFAULT_ACL = 'publicRead'
        MEDIA_URL = f'https://storage.googleapis.com/{GS_BUCKET_NAME}/media/'
        MEDIA_ROOT = 'media/'

if not GS_BUCKET_NAME:
    # Локально (якщо GCS не налаштовано або не знайдено ключ)
    print("OK: GCS не налаштовано, використовую локальне сховище 'media/'.")
    MEDIA_URL = 'media/'
    MEDIA_ROOT = BASE_DIR / 'media'


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