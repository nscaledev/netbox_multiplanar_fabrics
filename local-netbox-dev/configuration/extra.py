from os import environ


LOGIN_REQUIRED = environ.get("LOGIN_REQUIRED", "true").lower() == "true"
REMOTE_AUTH_ENABLED = False

# The Madison CAD underlay workflow accepts multi-megabyte DWG/PCP packages
# through NetBox's normal multipart form handling. Keep Django's request parser
# limit aligned above the plugin's CAD package cap so the plugin can return
# operator-facing validation messages instead of a generic HTTP 400.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(environ.get("DATA_UPLOAD_MAX_MEMORY_SIZE", str(3 * 1024 * 1024 * 1024)))
DATA_UPLOAD_MAX_NUMBER_FILES = int(environ.get("DATA_UPLOAD_MAX_NUMBER_FILES", "500"))
