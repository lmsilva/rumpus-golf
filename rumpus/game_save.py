"""In-progress game autosave (rumpus-game.json).

Written after every game event so a mid-round crash or quit can be resumed.
This is a plain snapshot — the ``Game`` object owns the live state; this file is
only its durable mirror.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .paths import GAME_PATH


class GameSave:
    @staticmethod
    def write(snapshot: dict[str, Any], path=GAME_PATH) -> None:
        try:
            path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def read(path=GAME_PATH) -> Optional[dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def clear(path=GAME_PATH) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
