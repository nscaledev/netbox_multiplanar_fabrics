from __future__ import annotations

import logging
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec

try:
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured
except ModuleNotFoundError:  # pragma: no cover - exercised by plain-Python contract tests
    settings = None

    class ImproperlyConfigured(Exception):
        pass


FLOORPLAN_MODULE = 'netbox_floorplan'
FLOORPLAN_PLUGIN_NAME = 'netbox_floorplan'
FLOORPLAN_PACKAGE = 'netbox-floorplan-plugin'

logger = logging.getLogger('netbox_plant_graph')


class FloorplanAvailabilityError(RuntimeError):
    """Raised when the floorplan plugin prerequisite is not satisfied."""


@dataclass(frozen=True)
class FloorplanAvailability:
    installed: bool
    enabled: bool
    version: str | None
    message: str

    @property
    def ready(self) -> bool:
        return self.installed and self.enabled


def _get_enabled_plugins() -> tuple[str, ...]:
    if settings is None:
        return ()
    try:
        return tuple(getattr(settings, 'PLUGINS', ()) or ())
    except ImproperlyConfigured:
        return ()


def is_floorplan_installed() -> bool:
    return find_spec(FLOORPLAN_MODULE) is not None


def is_floorplan_enabled() -> bool:
    return FLOORPLAN_PLUGIN_NAME in _get_enabled_plugins()


def get_floorplan_version() -> str | None:
    try:
        return version(FLOORPLAN_PACKAGE)
    except PackageNotFoundError:
        return None


def get_floorplan_availability() -> FloorplanAvailability:
    installed = is_floorplan_installed()
    enabled = is_floorplan_enabled()
    package_version = get_floorplan_version()

    if not installed:
        message = (
            "netbox-floorplan-plugin is required for the floorplan/layout integration work. "
            f"Install {FLOORPLAN_PACKAGE} and enable '{FLOORPLAN_PLUGIN_NAME}' in NetBox PLUGINS."
        )
    elif not enabled:
        version_suffix = f" ({package_version})" if package_version else ''
        message = (
            f"{FLOORPLAN_PACKAGE}{version_suffix} is installed but '{FLOORPLAN_PLUGIN_NAME}' is not enabled. "
            "Add it to NetBox PLUGINS before using the floorplan integration slices."
        )
    else:
        version_suffix = f" ({package_version})" if package_version else ''
        message = f"{FLOORPLAN_PACKAGE}{version_suffix} is installed and enabled."

    return FloorplanAvailability(
        installed=installed,
        enabled=enabled,
        version=package_version,
        message=message,
    )


def assert_floorplan_available() -> FloorplanAvailability:
    availability = get_floorplan_availability()
    if not availability.ready:
        raise FloorplanAvailabilityError(availability.message)
    return availability


def emit_floorplan_availability_warning() -> FloorplanAvailability:
    availability = get_floorplan_availability()
    if not availability.ready:
        logger.warning(availability.message)
    return availability
