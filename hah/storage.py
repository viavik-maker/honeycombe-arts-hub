"""JSON files under the data folder (content, inbox, subscribers, sessions).

Writes are atomic (temp file + fsync + rename). A file that can't be parsed
is never silently overwritten: it's moved aside as NAME.corrupt-<time> so it
can be recovered, and the caller gets the default."""
import json
import os
import sys
import threading
import time

from . import config

_lock = threading.RLock()


def path(name):
    return os.path.join(config.DATA, name)


def load_json(name, default):
    p = path(name)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return default
    except ValueError:
        with _lock:
            aside = "%s.corrupt-%s" % (p, time.strftime("%Y%m%d-%H%M%S"))
            try:
                os.replace(p, aside)
                sys.stderr.write("[storage] %s could not be read; kept as %s\n"
                                 % (name, os.path.basename(aside)))
            except OSError:
                pass
        return default


def _write(name, obj):
    tmp = path(name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path(name))


def save_json(name, obj):
    with _lock:
        _write(name, obj)


def update_json(name, default, fn):
    """Read-modify-write under one lock, so concurrent requests can't lose
    each other's changes. fn(current) returns the new value; returns that."""
    with _lock:
        new = fn(load_json(name, default))
        _write(name, new)
        return new


def replace_json(name, obj, backup=None):
    """Overwrite NAME, first copying its current contents to BACKUP."""
    with _lock:
        if backup:
            cur = load_json(name, None)
            if cur:
                _write(backup, cur)
        _write(name, obj)
