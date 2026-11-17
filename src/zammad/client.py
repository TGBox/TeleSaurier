"""Minimal client for the parts of the Zammad REST API TeleSaurier needs."""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from src.core.models import FaxError

Attachment = tuple[str, bytes]  # (file name, content)


@dataclass(frozen=True)
class TicketAttachment:
    ticket_id: int
    article_id: int
    attachment_id: int
    filename: str


def is_pdf(attachment: dict[str, Any]) -> bool:
    """Works for attachments from both the REST API and webhook payloads."""
    content_type = attachment.get("preferences", {}).get("Content-Type", "")
    return attachment.get("filename", "").lower().endswith(".pdf") or content_type.startswith(
        "application/pdf"
    )


def _encode(attachments: list[Attachment]) -> list[dict[str, str]]:
    return [
        {
            "filename": name,
            "data": base64.b64encode(content).decode("ascii"),
            "mime-type": "application/pdf",
        }
        for name, content in attachments
    ]


class ZammadClient:
    def __init__(self, url: str, token: str, timeout: float = 30) -> None:
        self._base = url.rstrip("/") + "/api/v1"
        self._token = token
        self._timeout = timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> bytes:
        request = urllib.request.Request(
            self._base + path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            method=method,
            headers={
                "Authorization": f"Token token={self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return response.read()
        except urllib.error.HTTPError as exc:
            raise FaxError(f"Zammad meldet Fehler {exc.code} bei {method} {path}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise FaxError(f"Zammad ist nicht erreichbar: {exc}") from exc

    def _json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        return json.loads(self._request(method, path, payload))

    def get_ticket(self, ticket_id: int) -> dict[str, Any]:
        return self._json("GET", f"/tickets/{ticket_id}")

    def find_ticket(self, number: str) -> dict[str, Any]:
        """Look a ticket up by the number agents see (not the internal id)."""
        query = urllib.parse.quote(f"number:{number.strip()}")
        result = self._json("GET", f"/tickets/search?query={query}&limit=1")
        # Depending on version and parameters Zammad answers with a list of tickets
        # or with {"tickets": [ids], ...}.
        if isinstance(result, list) and result:
            return result[0]
        if isinstance(result, dict) and result.get("tickets"):
            return self.get_ticket(result["tickets"][0])
        raise FaxError(f"Ticket {number} wurde nicht gefunden.")

    def pdf_attachments(self, ticket_id: int) -> list[TicketAttachment]:
        articles = self._json("GET", f"/ticket_articles/by_ticket/{ticket_id}")
        return [
            TicketAttachment(ticket_id, article["id"], attachment["id"], attachment["filename"])
            for article in articles
            for attachment in article.get("attachments", [])
            if is_pdf(attachment)
        ]

    def download(self, attachment: TicketAttachment) -> bytes:
        return self._request(
            "GET",
            f"/ticket_attachment/{attachment.ticket_id}/{attachment.article_id}"
            f"/{attachment.attachment_id}",
        )

    def add_note(
        self, ticket_id: int, subject: str, body: str, attachments: list[Attachment] | None = None
    ) -> None:
        self._request(
            "POST",
            "/ticket_articles",
            {
                "ticket_id": ticket_id,
                "subject": subject,
                "body": body,
                "content_type": "text/plain",
                "type": "note",
                "internal": True,
                "attachments": _encode(attachments or []),
            },
        )

    def create_ticket(
        self, title: str, group: str, customer: str, body: str, attachments: list[Attachment]
    ) -> dict[str, Any]:
        return self._json(
            "POST",
            "/tickets",
            {
                "title": title,
                "group": group,
                "customer": customer,
                "article": {
                    "subject": title,
                    "body": body,
                    "content_type": "text/plain",
                    "type": "note",
                    "internal": True,
                    "attachments": _encode(attachments),
                },
            },
        )
