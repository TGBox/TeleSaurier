"""The interfaces fax backends implement. Sending and receiving are separate,
because a device often delivers inbound faxes differently from how it accepts
outbound ones (e.g. send via the box, receive via a mailbox)."""

from typing import Protocol

from src.core.models import FaxJob, ReceivedFax, SendState


class FaxSender(Protocol):
    def send(self, job: FaxJob) -> str:
        """Submit the fax and return the backend's id for it. Raise on failure."""
        ...

    def status(self, remote_id: str) -> SendState:
        """Return the current state. Mark busy / no answer as ``retryable``."""
        ...


class FaxReceiver(Protocol):
    def fetch_received(self) -> list[ReceivedFax]:
        """Return received faxes. May return a fax again; callers dedupe by remote_id."""
        ...
