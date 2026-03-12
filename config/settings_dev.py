"""
Development settings.
"""

from .settings_common import *  # noqa: F401,F403


DEBUG = env_bool("DEBUG", True)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "127.0.0.1,localhost,.trycloudflare.com")
