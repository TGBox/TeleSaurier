"""Data types shared by all layers."""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class FaxStatus(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"
    RECEIVED = "received"

    @property
    def is_final(self) -> bool:
        return self in (FaxStatus.SENT, FaxStatus.FAILED, FaxStatus.RECEIVED)

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]


_STATUS_LABELS = {
    FaxStatus.QUEUED: "Wartet",
    FaxStatus.SENDING: "Wird gesendet",
    FaxStatus.SENT: "Gesendet",
    FaxStatus.FAILED: "Fehlgeschlagen",
    FaxStatus.RECEIVED: "Empfangen",
}


class Direction(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"

    @property
    def label(self) -> str:
        return "Ausgang" if self is Direction.OUTBOUND else "Eingang"


class FaxError(Exception):
    """A fax could not be processed. The message is shown to the user (German)."""


@dataclass(frozen=True)
class FaxJob:
    """A fax the user wants to send."""

    recipient: str
    document: Path
    subject: str = ""
    ticket_id: int | None = None
    origin: str | None = None  # where the job came from, e.g. "zammad-article:104"
    route: str | None = None  # name of the send route; None = the configured default


@dataclass(frozen=True)
class ReceivedFax:
    """A fax delivered by a transport."""

    remote_id: str
    sender: str
    document: Path
    received_at: datetime


@dataclass(frozen=True)
class JournalEntry:
    id: int
    direction: Direction
    number: str
    document: Path
    subject: str
    ticket_id: int | None
    status: FaxStatus
    detail: str
    remote_id: str | None
    route: str
    created_at: datetime
    updated_at: datetime


_SEPARATORS = re.compile(r"[\s\-/().]")


def normalize_fax_number(raw: str) -> str:
    """Strip common separators and validate; keeps a leading '+'.

    Dial prefixes are the transport's business, so nothing is rewritten here.
    """
    number = _SEPARATORS.sub("", raw)
    if not re.fullmatch(r"\+?\d{3,20}", number):
        raise FaxError(f"Ungültige Faxnummer: „{raw.strip()}“")
    return number
