"""Use cases: send a fax, track its status, collect received faxes."""

import shutil
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from src.core.journal import Journal
from src.core.models import (
    Direction,
    FaxError,
    FaxJob,
    FaxStatus,
    JournalEntry,
    normalize_fax_number,
)
from src.transports.base import FaxReceiver, FaxSender


def _check_pdf(path: Path) -> None:
    if not path.is_file():
        raise FaxError(f"Datei nicht gefunden: {path}")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise FaxError(f"„{path.name}“ ist keine PDF-Datei.")


class FaxService:
    """``senders`` and ``receivers`` map route names to backends; the first sender
    is the default route."""

    def __init__(
        self,
        journal: Journal,
        senders: dict[str, FaxSender],
        receivers: dict[str, FaxReceiver],
        documents_dir: Path,
    ) -> None:
        if not senders:
            raise ValueError("Es ist kein Fax-Weg zum Senden konfiguriert.")
        self.journal = journal
        self._senders = senders
        self._receivers = receivers
        self._documents_dir = documents_dir
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
        _check_pdf(job.document)
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
        )
        try:
            remote_id = self._senders[route].send(
                replace(job, recipient=number, document=stored, route=route)
            )
        except Exception as exc:  # noqa: BLE001 - any transport error must end up in the journal
            return self.journal.update(entry.id, FaxStatus.FAILED, str(exc))
        return self.journal.update(entry.id, FaxStatus.SENDING, remote_id=remote_id)

    def refresh(self) -> list[JournalEntry]:
        """Ask the routes about unfinished faxes; return entries whose status changed."""
        changed = []
        for entry in self.journal.unfinished():
            sender = self._senders.get(entry.route)
            if entry.remote_id is None or sender is None:
                continue
            status, detail = sender.status(entry.remote_id)
            if status != entry.status:
                changed.append(self.journal.update(entry.id, status, detail))
        return changed

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
