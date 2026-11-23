"""Test-only settings: SQLite, independent of MySQL and of any environment variables.

Imports only config/settings_base.py, so the MySQL driver, .env loading and the
DJANGO_SECRET_KEY / MYSQL_* checks in config/settings.py never run for tests.
Use with --settings=config.settings_test.
"""

from .settings_base import *  # noqa: F401,F403
from .settings_base import BASE_DIR

# Fixed key for tests only. Never used outside test runs.
SECRET_KEY = 'frameflow-test-only-secret-key-not-for-production'

DEBUG = False

ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'frameflow_test.sqlite3',
    }
}
