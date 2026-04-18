from netbox.settings import *  # noqa: F401,F403


CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'netbox-plant-graph-tests',
    }
}