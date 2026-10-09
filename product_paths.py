"""Read-only application resources and private, writable user data."""
import os
from pathlib import Path
import sys

FROZEN = bool(getattr(sys, 'frozen', False))
RESOURCE_ROOT = Path(__file__).resolve().parent
APP_ROOT = Path(sys.executable).resolve().parent if FROZEN else RESOURCE_ROOT
ENGINE_DIR = APP_ROOT / 'katago_engine'


def user_data_dir():
    # Explicit override is useful for isolated acceptance tests and local backups.
    override = os.environ.get('YIXI_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if FROZEN:
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'YixiGo'
    return APP_ROOT / 'product_data'


DATA_DIR = user_data_dir()
