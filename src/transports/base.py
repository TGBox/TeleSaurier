"""The interface every fax backend has to implement."""

from typing import Protocol

from src.core.models import FaxJob, FaxStatus, ReceivedFax


class FaxTransport(Protocol):
    def send(self, job: FaxJob) -> str:
        """Submit the fax and return the backend's id for it. Raise on failure."""
        ...

    def status(self, remote_id: str) -> tuple[FaxStatus, str]:
        """Return the current status and a human-readable detail (German)."""
        ...

    def fetch_received(self) -> list[ReceivedFax]:
        """Return received faxes. May return a fax again; callers dedupe by remote_id."""
        ...
