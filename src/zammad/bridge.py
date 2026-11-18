"""Workflows that connect the fax service with Zammad tickets."""

import logging
import tempfile
from pathlib import Path
from typing import Any

from src.config import ZammadConfig
from src.core.models import FaxError, FaxJob, FaxStatus, JournalEntry
from src.core.service import FaxService
from src.zammad.client import TicketAttachment, ZammadClient, is_pdf

log = logging.getLogger(__name__)

# Every note TeleSaurier writes starts with this, so its own notes never trigger a fax.
NOTE_PREFIX = "TeleSaurier:"
_NOT_SENT = f"{NOTE_PREFIX} Fax nicht versendet"


def is_own_note(article: dict[str, Any]) -> bool:
    return (article.get("subject") or "").startswith(NOTE_PREFIX)


def send_from_webhook(
    service: FaxService, client: ZammadClient, config: ZammadConfig, payload: dict[str, Any]
) -> list[JournalEntry]:
    """Fax the PDF attachments of the triggering article to the ticket's fax number."""
    ticket, article = payload["ticket"], payload["article"]
    ticket_id = ticket["id"]
    origin = f"zammad-article:{article['id']}"
    if is_own_note(article) or service.journal.has_origin(origin):
        return []
    recipient = str(ticket.get(config.fax_number_field) or "").strip()
    pdfs = [a for a in article.get("attachments", []) if is_pdf(a)]
    problem = None
    if not recipient:
        problem = f"Im Ticket ist keine Faxnummer hinterlegt ({config.fax_number_field})."
    elif not pdfs:
        problem = "Der Artikel enthält keinen PDF-Anhang."
    if problem:
        client.add_note(ticket_id, _NOT_SENT, problem)
        return []
    entries = []
    with tempfile.TemporaryDirectory() as tmp:
        for pdf in pdfs:
            attachment = TicketAttachment(ticket_id, article["id"], pdf["id"], pdf["filename"])
            path = Path(tmp) / f"{pdf['id']}.pdf"
            try:
                path.write_bytes(client.download(attachment))
                job = FaxJob(
                    recipient, path, pdf["filename"], ticket_id, origin, user=_author(article)
                )
                entry = service.send(job)
            except FaxError as exc:
                client.add_note(ticket_id, _NOT_SENT, f"{pdf['filename']}: {exc}")
                continue
            entries.append(entry)
            if entry.status is FaxStatus.FAILED:
                report_result(service, client, entry)
    return entries


def _author(article: dict[str, Any]) -> str:
    """Login of the agent who wrote the article; the payload format varies."""
    author = article.get("created_by")
    if isinstance(author, dict):
        author = author.get("login") or author.get("email")
    return str(author or "zammad")


def report_result(service: FaxService, client: ZammadClient, entry: JournalEntry) -> None:
    """Write the final outcome of a ticket-related fax into the ticket, report attached."""
    if entry.ticket_id is None or not entry.status.is_final:
        return
    if entry.status is FaxStatus.SENT:
        subject = f"{NOTE_PREFIX} Fax gesendet"
        body = f"„{entry.subject}“ wurde an {entry.number} gefaxt."
    else:
        subject = f"{NOTE_PREFIX} Fax fehlgeschlagen"
        body = f"„{entry.subject}“ konnte nicht an {entry.number} gefaxt werden."
    if entry.detail:
        body += f"\n{entry.detail}"
    report = (f"sendebericht-{entry.id}.pdf", service.report(entry))
    client.add_note(entry.ticket_id, subject, body, [report])


def inbound_to_ticket(client: ZammadClient, config: ZammadConfig, entry: JournalEntry) -> None:
    """Create a ticket for a received fax with the PDF attached."""
    client.create_ticket(
        title=f"Fax von {entry.number}",
        group=config.inbound_group,
        customer=config.inbound_customer,
        body=f"Eingegangenes Fax von {entry.number}.",
        attachments=[(f"fax-{entry.id}.pdf", entry.document.read_bytes())],
    )


def sync(service: FaxService, client: ZammadClient, config: ZammadConfig) -> None:
    """One polling round: report finished faxes, turn received faxes into tickets."""
    for entry in service.refresh():
        _logged(report_result, service, client, entry)
    for entry in service.poll_inbound():
        _logged(inbound_to_ticket, client, config, entry)


def _logged(action: Any, *args: Any) -> None:
    try:
        action(*args)
    except FaxError:
        log.exception("Zammad update failed")
