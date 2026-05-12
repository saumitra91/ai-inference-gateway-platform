"""Shared Django settings. Environment-specific files import this module with `from .base import *`."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ImproperlyConfigured(f"Invalid integer for {name}: {raw!r}") from exc


def database_from_url(url: str) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured("DATABASE_URL must be a postgres:// or postgresql:// URL")
    db_name = (parsed.path or "").lstrip("/")
    if not db_name:
        raise ImproperlyConfigured("DATABASE_URL must include a database name")
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": db_name,
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or 5432),
        "CONN_MAX_AGE": _env_int("DB_CONN_MAX_AGE", 60),
        "OPTIONS": {},
    }


SECRET_KEY = os.environ.get("SECRET_KEY", "unsafe-development-secret-key")
DEBUG = _env_bool("DEBUG", default=False)

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "apps.users",
    "apps.inference",
    "apps.observability",
    "apps.api_keys",
    "apps.benchmarks",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.observability.middleware.RequestContextMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.observability.middleware.BodySizeLimitMiddleware",
    "apps.observability.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

CORS_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
CORS_ALLOW_CREDENTIALS = True

# API key hashing pepper (defaults to SECRET_KEY if unset — prefer a dedicated secret in production).
API_KEY_HMAC_PEPPER = os.environ.get("API_KEY_HMAC_PEPPER", "")

# Inference / logging controls
DEBUG_LOG_FULL_PROMPTS = _env_bool("DEBUG_LOG_FULL_PROMPTS", default=False)
LOG_RETENTION_DAYS = _env_int("LOG_RETENTION_DAYS", default=30)
INFERENCE_MAX_REQUEST_BODY_BYTES = _env_int("INFERENCE_MAX_REQUEST_BODY_BYTES", default=2_000_000)
INFERENCE_UPSTREAM_TIMEOUT_S = float(os.environ.get("INFERENCE_UPSTREAM_TIMEOUT_S", "600"))
READINESS_INCLUDE_LLAMA = _env_bool("READINESS_INCLUDE_LLAMA", default=False)

# Generation controls — applied server-side as defaults and hard caps
INFERENCE_DEFAULT_MAX_TOKENS = _env_int("INFERENCE_DEFAULT_MAX_TOKENS", 1024)
INFERENCE_HARD_MAX_TOKENS = _env_int("INFERENCE_HARD_MAX_TOKENS", 4096)
INFERENCE_DEFAULT_TEMPERATURE = float(os.environ.get("INFERENCE_DEFAULT_TEMPERATURE", "0.7"))
INFERENCE_DEFAULT_TOP_P = float(os.environ.get("INFERENCE_DEFAULT_TOP_P", "0.9"))

# Prompt safeguards
INFERENCE_MAX_PROMPT_CHARS = _env_int("INFERENCE_MAX_PROMPT_CHARS", 100_000)
ENABLE_CROSS_ORIGIN_OPENER_POLICY = _env_bool("ENABLE_CROSS_ORIGIN_OPENER_POLICY", default=False)

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {"default": database_from_url(DATABASE_URL)}

REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
if _env_bool("USE_REDIS", default=False):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
}

REQUEST_ID_HEADER = os.environ.get("REQUEST_ID_HEADER", "X-Request-ID")

# llama.cpp OpenAI-compatible HTTP server (never load GGUF inside Django workers).
LLAMA_CPP_BASE_URL = os.environ.get("LLAMA_CPP_BASE_URL", "http://127.0.0.1:8080")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "fmt": "%(levelname)s %(name)s %(message)s %(request_id)s",
        },
    },
    "filters": {
        "request_id": {
            "()": "apps.observability.logging.RequestIdFilter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["request_id"],
        },
    },
    "root": {
        "handlers": ["console"],
        "level": LOG_LEVEL,
    },
    "loggers": {
        "django.request": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "apps": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "httpx": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
