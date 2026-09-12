"""User settings (rumpus-settings.json).

Defaults mirror ``data/settings.schema.json``. The settings object is the single
place the UI's Settings screen reads from and writes to; the backend applies the
relevant bits (theme is cosmetic on the client, music/SFX/rumble/feed are
applied by the backend and the browser).
"""
from __future__ import annotations

import copy
import json
from typing import Any

from .paths import SETTINGS_PATH


DEFAULTS: dict[str, Any] = {
    "theme": "dark",                       # dark | light | auto
    "music": {
        "enabled": True,
        "volume": 0.62,
        "playlist": "lounge-jazz",          # lounge-jazz | lofi-hiphop | both-shuffled | custom
        "customFolder": None,
    },
    "sfx": {"enabled": True, "volume": 0.8, "announcer": True},
    "controller": {"rumble": True},
    "display": {"showCameraFeed": True},
    "rules": {"holes": 3, "strokeCap": 8, "oobPenalty": True, "tunnelBonus": True},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    def __init__(self) -> None:
        self._data = copy.deepcopy(DEFAULTS)
        self.load()

    # -- persistence -------------------------------------------------------- #
    def load(self) -> None:
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            self._data = _deep_merge(DEFAULTS, raw)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            self._data = copy.deepcopy(DEFAULTS)

    def save(self) -> None:
        try:
            SETTINGS_PATH.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    # -- access ------------------------------------------------------------- #
    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def get(self, *path: str, default: Any = None) -> Any:
        node: Any = self._data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def set(self, value: Any, *path: str) -> None:
        if not path:
            return
        node = self._data
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value

    # -- convenience -------------------------------------------------------- #
    @property
    def holes(self) -> int:
        return int(self.get("rules", "holes", default=3))

    @property
    def stroke_cap(self) -> int:
        return int(self.get("rules", "strokeCap", default=8))

    @property
    def theme(self) -> str:
        return str(self.get("theme", default="dark"))
