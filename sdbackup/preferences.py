"""Persistent UI preferences, separate from touchscreen calibration."""
import json
import os
from pathlib import Path
import tempfile

from .i18n import LANGUAGES


def load_language(path):
    try:
        language = json.loads(Path(path).read_text(encoding='utf-8')).get('language', 'fr')
        return language if language in LANGUAGES else 'fr'
    except (OSError, ValueError, AttributeError, TypeError):
        return 'fr'


def save_language(path, language):
    if language not in LANGUAGES:
        raise ValueError('Unsupported language')
    save_setting(path, 'language', language)


def load_orientation(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8')).get('orientation', 0)
        return value if type(value) is int and value in (0, 90, 180, 270) else 0
    except (OSError, ValueError, AttributeError, TypeError):
        return 0


def save_orientation(path, orientation):
    if type(orientation) is not int or orientation not in (0, 90, 180, 270):
        raise ValueError('Unsupported orientation')
    save_setting(path, 'orientation', orientation)


def save_setting(path, key, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        settings = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(settings, dict):
            settings = {}
    except (OSError, ValueError):
        settings = {}
    settings[key] = value
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.settings-', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(settings, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
