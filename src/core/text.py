"""Small formatting helpers for user-facing text."""

from datetime import datetime


def local_time(moment: datetime) -> str:
    return moment.astimezone().strftime("%d.%m.%Y %H:%M")
