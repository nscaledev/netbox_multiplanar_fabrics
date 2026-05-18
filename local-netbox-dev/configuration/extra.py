from os import environ


LOGIN_REQUIRED = environ.get("LOGIN_REQUIRED", "true").lower() == "true"
REMOTE_AUTH_ENABLED = False

