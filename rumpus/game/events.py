"""Append-only event log — the undo stack and the score history.

Every game action that changes a score or turn is recorded. Undo pops the latest
event of the *current hole* and reverts its effect.
"""
from __future__ import annotations

import time
from typing import Any

from ..models import Event, EventType


class EventLog:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def append(self, type_: EventType, player_id: str, hole: int, **data) -> Event:
        e = Event(type=type_, player_id=player_id, hole=hole, t=time.time(), data=data)
        self.events.append(e)
        return e

    def events_for_hole(self, hole: int) -> list[Event]:
        return [e for e in self.events if e.hole == hole]

    def pop_last_for_hole(self, hole: int) -> Event | None:
        for i in range(len(self.events) - 1, -1, -1):
            if self.events[i].hole == hole:
                return self.events.pop(i)
        return None

    def as_list(self) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self.events]

    def clear(self) -> None:
        self.events.clear()
