"""Stand-in backend for development: nothing leaves the machine.

Sent faxes are copied to ``<base>/outbox``. Numbers ending in ``0000`` fail, so the
error path can be tried out. A PDF dropped into ``<base>/inbox`` is "received"; its
file name (without ``.pdf``) is used as the sender number.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.core.models import FaxJob, FaxStatus, ReceivedFax

FAILING_SUFFIX = "0000"


class SimulatedTransport:
    def __init__(self, base_dir: Path) -> None:
        self.outbox = base_dir / "outbox"
        self.inbox = base_dir / "inbox"
        self._received = base_dir / "received"
        for folder in (self.outbox, self.inbox, self._received):
            folder.mkdir(parents=True, exist_ok=True)

    def send(self, job: FaxJob) -> str:
        remote_id = uuid4().hex
        shutil.copyfile(job.document, self.outbox / f"{remote_id}.pdf")
        meta = {"recipient": job.recipient, "subject": job.subject}
        (self.outbox / f"{remote_id}.json").write_text(json.dumps(meta), encoding="utf-8")
        return remote_id

    def status(self, remote_id: str) -> tuple[FaxStatus, str]:
        meta = json.loads((self.outbox / f"{remote_id}.json").read_text(encoding="utf-8"))
        if meta["recipient"].endswith(FAILING_SUFFIX):
            return FaxStatus.FAILED, "Simulierter Fehler: Gegenstelle nicht erreichbar"
        return FaxStatus.SENT, "Simuliert – es wurde kein echtes Fax versendet"

    def fetch_received(self) -> list[ReceivedFax]:
        faxes = []
        for source in sorted(self.inbox.glob("*.pdf")):
            remote_id = uuid4().hex
            target = self._received / f"{remote_id}.pdf"
            shutil.move(source, target)
            faxes.append(ReceivedFax(remote_id, source.stem, target, datetime.now(UTC)))
        return faxes
