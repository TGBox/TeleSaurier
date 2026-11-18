"""Stand-in backend for development: nothing leaves the machine.

Sent faxes are copied to ``<base>/outbox``. The last digits of the number select
the outcome, so every path can be tried out:

- ``...0000``  fails for good
- ``...0001``  is always busy (retried until the attempts are used up)
- ``...0002``  is busy on the first attempt and goes through on the second

A PDF dropped into ``<base>/inbox`` is "received"; its file name (without
``.pdf``) is used as the sender number.
"""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.core.models import FaxJob, FaxStatus, ReceivedFax, SendState

FAILING_SUFFIX = "0000"
BUSY_SUFFIX = "0001"
BUSY_ONCE_SUFFIX = "0002"
_BUSY = SendState(FaxStatus.FAILED, "Simuliert: Gegenstelle besetzt", retryable=True)


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
        # The stored document keeps its name across retries, so it identifies the fax.
        earlier = [
            meta
            for meta in map(self._meta, self.outbox.glob("*.json"))
            if meta.get("document") == job.document.name
        ]
        meta = {
            "recipient": job.recipient,
            "subject": job.subject,
            "document": job.document.name,
            "attempt": len(earlier) + 1,
        }
        (self.outbox / f"{remote_id}.json").write_text(json.dumps(meta), encoding="utf-8")
        return remote_id

    @staticmethod
    def _meta(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def status(self, remote_id: str) -> SendState:
        meta = self._meta(self.outbox / f"{remote_id}.json")
        recipient = meta["recipient"]
        if recipient.endswith(FAILING_SUFFIX):
            return SendState(FaxStatus.FAILED, "Simulierter Fehler: Gegenstelle nicht erreichbar")
        if recipient.endswith(BUSY_SUFFIX):
            return _BUSY
        if recipient.endswith(BUSY_ONCE_SUFFIX) and meta["attempt"] == 1:
            return _BUSY
        return SendState(FaxStatus.SENT, "Simuliert – es wurde kein echtes Fax versendet")

    def fetch_received(self) -> list[ReceivedFax]:
        faxes = []
        for source in sorted(self.inbox.glob("*.pdf")):
            remote_id = uuid4().hex
            target = self._received / f"{remote_id}.pdf"
            shutil.move(source, target)
            faxes.append(ReceivedFax(remote_id, source.stem, target, datetime.now(UTC)))
        return faxes
