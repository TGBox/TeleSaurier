"""Use cases: send a fax, track its status, retry, collect received faxes."""

import getpass
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from src.core.journal import Journal
from src.core.models import (
    Direction,
    FaxError,
    FaxJob,
    FaxStatus,
    JournalEntry,
    normalize_fax_number,
)
from src.core.report import build_report
from src.core.text import local_time
from src.transports import route_label
from src.transports.base import FaxReceiver, FaxSender


def count_pages(path: Path) -> int:
    """Number of pages; raises FaxError if the file is missing or not a readable PDF."""
    if not path.is_file():
        raise FaxError(f"Datei nicht gefunden: {path}")
    try:
        return len(PdfReader(path).pages)
    except (PyPdfError, ValueError, OSError) as exc:
        raise FaxError(f"„{path.name}“ ist keine lesbare PDF-Datei.") from exc


class FaxService:
    """``senders`` and ``receivers`` map route names to backends; the first sender
    is the default route."""

    def __init__(
        self,
        journal: Journal,
        senders: dict[str, FaxSender],
        receivers: dict[str, FaxReceiver],
        documents_dir: Path,
        *,
        max_attempts: int = 3,
        retry_pause: timedelta = timedelta(minutes=2),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not senders:
            raise ValueError("Es ist kein Fax-Weg zum Senden konfiguriert.")
        self.journal = journal
        self._senders = senders
        self._receivers = receivers
        self._documents_dir = documents_dir
        self._max_attempts = max_attempts
        self._retry_pause = retry_pause
        self._clock = clock
        documents_dir.mkdir(parents=True, exist_ok=True)

    @property
    def send_routes(self) -> list[str]:
        """Names of the send routes, default first."""
        return list(self._senders)

    def send(self, job: FaxJob) -> JournalEntry:
        """Validate the job, keep a copy of the document and hand it to the route.

        Raises FaxError for invalid input. A transport failure does not raise; it is
        recorded as a FAILED journal entry so the attempt stays visible.
        """
        route = job.route or self.send_routes[0]
        if route not in self._senders:
            raise FaxError(f"Der Fax-Weg „{route}“ ist nicht konfiguriert.")
        number = normalize_fax_number(job.recipient)
        pages = count_pages(job.document)
        stored = self._documents_dir / f"{uuid4().hex}.pdf"
        shutil.copyfile(job.document, stored)
        entry = self.journal.add(
            Direction.OUTBOUND,
            number,
            stored,
            FaxStatus.QUEUED,
            route,
            subject=job.subject,
            ticket_id=job.ticket_id,
            origin=job.origin,
            user=job.user or getpass.getuser(),
            pages=pages,
        )
        return self._submit(entry)

    def _submit(self, entry: JournalEntry) -> JournalEntry:
        job = FaxJob(
            entry.number, entry.document, entry.subject, entry.ticket_id, route=entry.route
        )
        attempts = entry.attempts + 1
        try:
            remote_id = self._senders[entry.route].send(job)
        except Exception as exc:  # noqa: BLE001 - any transport error must end up in the journal
            return self.journal.update(entry.id, FaxStatus.FAILED, str(exc), attempts=attempts)
        return self.journal.update(
            entry.id, FaxStatus.SENDING, remote_id=remote_id, attempts=attempts
        )

    def refresh(self) -> list[JournalEntry]:
        """Advance unfinished faxes: query their status, schedule and start retries.

        Returns the entries whose status changed.
        """
        changed = []
        for entry in self.journal.unfinished():
            if entry.route not in self._senders:
                continue
            if entry.status is FaxStatus.QUEUED:
                if entry.next_attempt_at and entry.next_attempt_at <= self._clock():
                    changed.append(self._submit(entry))
                continue
            if entry.remote_id is None:
                continue
            state = self._senders[entry.route].status(entry.remote_id)
            if (
                state.status is FaxStatus.FAILED
                and state.retryable
                and entry.attempts < self._max_attempts
            ):
                retry_at = self._clock() + self._retry_pause
                detail = (
                    f"{state.detail} – Versuch {entry.attempts + 1} von {self._max_attempts} "
                    f"um {local_time(retry_at)[-5:]} Uhr"
                )
                changed.append(
                    self.journal.update(
                        entry.id, FaxStatus.QUEUED, detail, next_attempt_at=retry_at
                    )
                )
            elif state.status != entry.status:
                changed.append(self.journal.update(entry.id, state.status, state.detail))
        return changed

    def report(self, entry: JournalEntry) -> bytes:
        """Transmission report (PDF) for a finished outbound fax."""
        if entry.direction is not Direction.OUTBOUND or not entry.status.is_final:
            raise FaxError("Einen Sendebericht gibt es erst für abgeschlossene Ausgänge.")
        return build_report(entry, route_label(entry.route))

    def poll_inbound(self) -> list[JournalEntry]:
        """Collect newly received faxes from all receive routes; return the new entries."""
        new = []
        for route, receiver in self._receivers.items():
            for fax in receiver.fetch_received():
                if self.journal.has_inbound(fax.remote_id):
                    continue
                new.append(
                    self.journal.add(
                        Direction.INBOUND,
                        fax.sender,
                        fax.document,
                        FaxStatus.RECEIVED,
                        route,
                        remote_id=fax.remote_id,
                    )
                )
        return new
