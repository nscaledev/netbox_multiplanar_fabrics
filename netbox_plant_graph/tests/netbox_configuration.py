import os
from copy import deepcopy

from netbox.configuration_testing import *  # noqa: F401,F403


DEVELOPER = True


if 'DATABASE' in globals():
    DATABASE = deepcopy(DATABASE)
    DATABASE.update({
        'NAME': os.getenv('NETBOX_TEST_DB_NAME', DATABASE.get('NAME')),
        'USER': os.getenv('NETBOX_TEST_DB_USER', DATABASE.get('USER')),
        'PASSWORD': os.getenv('NETBOX_TEST_DB_PASSWORD', DATABASE.get('PASSWORD')),
        'HOST': os.getenv('NETBOX_TEST_DB_HOST', DATABASE.get('HOST')),
        'PORT': os.getenv('NETBOX_TEST_DB_PORT', DATABASE.get('PORT')),
    })
    DATABASE.setdefault('TEST', {})
    DATABASE['TEST']['NAME'] = os.getenv(
        'NETBOX_TEST_DB_TEST_NAME',
        f"test_{DATABASE['NAME']}_plant_graph",
    )
    # Keep a Django-native alias for internal consumers expecting DATABASES.
    DATABASES = {'default': DATABASE}
else:
    DATABASES = deepcopy(DATABASES)
    DATABASES['default'].update({
        'NAME': os.getenv('NETBOX_TEST_DB_NAME', DATABASES['default']['NAME']),
        'USER': os.getenv('NETBOX_TEST_DB_USER', DATABASES['default']['USER']),
        'PASSWORD': os.getenv('NETBOX_TEST_DB_PASSWORD', DATABASES['default']['PASSWORD']),
        'HOST': os.getenv('NETBOX_TEST_DB_HOST', DATABASES['default']['HOST']),
        'PORT': os.getenv('NETBOX_TEST_DB_PORT', DATABASES['default']['PORT']),
    })
    DATABASES['default'].setdefault('TEST', {})
    DATABASES['default']['TEST']['NAME'] = os.getenv(
        'NETBOX_TEST_DB_TEST_NAME',
        f"test_{DATABASES['default']['NAME']}_plant_graph",
    )

REDIS = deepcopy(REDIS)
for section_name in ('tasks', 'caching'):
    REDIS[section_name].update({
        'HOST': os.getenv('NETBOX_TEST_REDIS_HOST', REDIS[section_name]['HOST']),
        'PORT': os.getenv('NETBOX_TEST_REDIS_PORT', REDIS[section_name]['PORT']),
        'PASSWORD': os.getenv('NETBOX_TEST_REDIS_PASSWORD', REDIS[section_name]['PASSWORD']),
    })

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'netbox-plant-graph-tests',
    }
}

PLUGINS = [
    plugin_name
    for plugin_name in PLUGINS
    if plugin_name not in ('netbox_floorplan', 'netbox_plant_graph')
]
PLUGINS.extend(['netbox_floorplan', 'netbox_plant_graph'])

PLUGINS_CONFIG = {
    **globals().get('PLUGINS_CONFIG', {}),
    'netbox_plant_graph': {
        'top_level_menu': True,
    },
}
