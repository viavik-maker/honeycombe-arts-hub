"""JSON files under the data folder (content, inbox, subscribers, sessions)."""
import json
import os
import threading

from . import config

_lock = threading.Lock()


def path(name):
    return os.path.join(config.DATA, name)


def load_json(name, default):
    try:
        with open(path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(name, obj):
    with _lock:
        tmp = path(name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path(name))
