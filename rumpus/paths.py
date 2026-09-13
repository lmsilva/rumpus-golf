"""Filesystem paths for Rumpus Golf.

All runtime state lives next to the package root so it is easy to find and
back up. ``data/`` holds shipped (read-only) data; the ``rumpus-*.json`` files
hold per-machine user data created at runtime.
"""
from __future__ import annotations

from pathlib import Path

# The repository / project root (parent of the ``rumpus`` package).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Shipped data (read-only). ``data/courses.json`` etc. live here.
DATA_DIR = PROJECT_ROOT / "data"

# The web front-end served by the backend.
WEB_DIR = PROJECT_ROOT / "web"

# Runtime persistence (created lazily).
SETUP_PATH = PROJECT_ROOT / "rumpus-setup.json"     # calibration + obstacles + balls + players
SETTINGS_PATH = PROJECT_ROOT / "rumpus-settings.json"  # user preferences
GAME_PATH = PROJECT_ROOT / "rumpus-game.json"       # in-progress game autosave
REFERENCE_PATH = PROJECT_ROOT / "rumpus-floor.png"  # empty-floor picture (webcam path)


def ensure_runtime_dirs() -> None:
    """Create any directories the app writes into at runtime."""
    (WEB_DIR / "assets" / "photos").mkdir(parents=True, exist_ok=True)
    (WEB_DIR / "assets" / "music").mkdir(parents=True, exist_ok=True)
    (WEB_DIR / "assets" / "sfx").mkdir(parents=True, exist_ok=True)
    (WEB_DIR / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
