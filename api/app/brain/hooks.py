"""Seam for a future continuous cognition process.

Observers may watch memory and question events, then form candidate thoughts.
This module never sends those thoughts to the user. Outward messages stay in
the chat runtime, which decides separately whether anything is worth saying.
"""

from __future__ import annotations

from collections.abc import Callable

Observer = Callable[[dict], None]
_observers: list[Observer] = []


def register(observer: Observer) -> None:
    _observers.append(observer)


def notify(event: dict) -> None:
    for observer in list(_observers):
        try:
            observer(event)
        except Exception:
            continue
