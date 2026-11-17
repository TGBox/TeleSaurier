"""Use cases: send a fax, track its status, collect received faxes."""

import shutil
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
from src.transports.base import FaxTransport


def _check_pdf(path: Path) -> None:
    if not path.is_file():
        raise FaxError(f"Datei nicht gefunden: {path}")
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise FaxError(f"„{path.name}“ ist keine PDF-Datei.")


class FaxService:
    def __init__(self, journal: Journal, transport: FaxTransport, documents_dir: Path) -> None:
        self.journal = journal
        self._transport = transport
        self._documents_dir = documents_dir
        documents_dir.mkdir(parents=True, exist_ok=True)

    def send(self, job: FaxJob) -> JournalEntry:
        """Validate the job, keep a copy of the document and hand it to the transport.

        Raises FaxError for invalid input. A transport failure does not raise; it is
        recorded as a FAILED journal entry so the attempt stays visible.
        """
        number = normalize_fax_number(job.recipient)
        _check_pdf(job.document)
        stored = self._documents_dir / f"{uuid4().hex}.pdf"
        shutil.copyfile(job.document, stored)
        stored_job = FaxJob(number, stored, job.subject, job.ticket_id, job.origin)
        entry = self.journal.add(
            Direction.OUTBOUND,
            number,
            stored,
            FaxStatus.QUEUED,
            subject=job.subject,
            ticket_id=job.ticket_id,
            origin=job.origin,
        )
        try:
            remote_id = self._transport.send(stored_job)
        except Exception as exc:  # noqa: BLE001 - any transport error must end up in the journal
            return self.journal.update(entry.id, FaxStatus.FAILED, str(exc))
        return self.journal.update(entry.id, FaxStatus.SENDING, remote_id=remote_id)

    def refresh(self) -> list[JournalEntry]:
        """Ask the transport about unfinished faxes; return entries whose status changed."""
        changed = []
        for entry in self.journal.unfinished():
            if entry.remote_id is None:
                continue
            status, detail = self._transport.status(entry.remote_id)
            if status != entry.status:
                changed.append(self.journal.update(entry.id, status, detail))
        return changed

    def poll_inbound(self) -> list[JournalEntry]:
        """Collect newly received faxes; return the new journal entries."""
        new = []
        for fax in self._transport.fetch_received():
            if self.journal.has_inbound(fax.remote_id):
                continue
            new.append(
                self.journal.add(
                    Direction.INBOUND,
                    fax.sender,
                    fax.document,
                    FaxStatus.RECEIVED,
                    remote_id=fax.remote_id,
                )
            )
        return new
