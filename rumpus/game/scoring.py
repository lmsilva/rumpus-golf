"""Scorecard, standings and night-stats computations (pure functions)."""
from __future__ import annotations

from typing import Any


def _player_dict(p: Any) -> dict[str, Any]:
    """Accept a Player dataclass or a plain dict from the UI snapshot."""
    if isinstance(p, dict):
        return p
    if hasattr(p, "as_dict"):
        return p.as_dict()
    return {
        "id": getattr(p, "id", ""),
        "name": getattr(p, "name", ""),
        "color": getattr(p, "color", ""),
    }


def strokes_per_hole(player_scores: dict[str, list[int | None]], player_id: str) -> list[int | None]:
    return player_scores.get(player_id, [])


def total(player_scores: dict[str, list[int | None]], player_id: str) -> int:
    return sum(s for s in player_scores.get(player_id, []) if s is not None)


def par_for(course_pars: list[int]) -> int:
    return sum(course_pars)


def vs_par(total_strokes: int, par: int) -> str:
    diff = total_strokes - par
    if diff == 0:
        return "even"
    return f"{'+' if diff > 0 else ''}{diff}"


def standings(players: list, player_scores: dict[str, list[int | None]]) -> list[dict]:
    """Players sorted by ascending total; ties by fewest strokes on last hole."""
    rows = [_player_dict(p) for p in players]
    def key(p):
        pid = p["id"]
        sc = player_scores.get(pid, [])
        last = [s for s in sc if s is not None]
        return (total(player_scores, pid), last[-1] if last else 0)
    return sorted(rows, key=key)


def build_scorecard(players: list, player_scores: dict[str, list[int | None]],
                    hole_count: int, course_pars: list[int]) -> dict[str, Any]:
    rows = []
    for raw in players:
        p = _player_dict(raw)
        pid = p.get("id", "")
        sc = player_scores.get(pid, [])
        rows.append({
            "id": pid, "name": p.get("name", ""), "color": p.get("color", ""),
            "scores": sc, "total": total(player_scores, pid),
        })
    return {
        "players": rows,
        "holes": list(range(1, hole_count + 1)),
        "pars": course_pars,
        "par_total": sum(course_pars),
    }
