"""Scorecard, standings and night-stats computations (pure functions)."""
from __future__ import annotations

from typing import Any


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


def standings(players: list[dict], player_scores: dict[str, list[int | None]]) -> list[dict]:
    """Players sorted by ascending total; ties by fewest strokes on last hole."""
    def key(p):
        pid = p["id"]
        sc = player_scores.get(pid, [])
        last = [s for s in sc if s is not None]
        return (total(player_scores, pid), last[-1] if last else 0)
    ordered = sorted(players, key=key)
    return ordered


def build_scorecard(players: list[dict], player_scores: dict[str, list[int | None]],
                    hole_count: int, course_pars: list[int]) -> dict[str, Any]:
    rows = []
    for p in players:
        sc = player_scores.get(p["id"], [])
        rows.append({
            "id": p["id"], "name": p["name"], "color": p["color"],
            "scores": sc, "total": total(player_scores, p["id"]),
        })
    return {
        "players": rows,
        "holes": list(range(1, hole_count + 1)),
        "pars": course_pars,
        "par_total": sum(course_pars),
    }
