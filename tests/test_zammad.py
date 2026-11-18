"""End-to-end tests against a fake Zammad served over real HTTP."""

import base64
import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest
from pypdf import PdfReader

from src.config import ZammadConfig
from src.core.models import FaxError, FaxStatus
from src.server.app import WEBHOOK_PATH, make_server
from src.zammad import bridge
from src.zammad.client import ZammadClient
from tests.conftest import make_pdf

SECRET = "s3cret"
PDF = make_pdf()
INBOUND = b"%PDF-1.4 inbound"
ARTICLE = {
    "id": 104,
    "subject": "Bitte faxen",
    "created_by": {"login": "agent.anna"},
    "attachments": [
        {"id": 174, "filename": "befund.pdf", "preferences": {"Content-Type": "application/pdf"}},
        {"id": 175, "filename": "logo.png", "preferences": {"Content-Type": "image/png"}},
    ],
}


class FakeZammad:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.tokens: set[str] = set()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, body: bytes, status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                fake.tokens.add(self.headers["Authorization"])
                if self.path.startswith("/api/v1/tickets/search"):
                    self._send(json.dumps({"tickets": [81]}).encode())
                elif self.path == "/api/v1/tickets/81":
                    self._send(json.dumps({"id": 81, "number": "10081"}).encode())
                elif self.path == "/api/v1/ticket_articles/by_ticket/81":
                    self._send(json.dumps([ARTICLE]).encode())
                elif self.path == "/api/v1/ticket_attachment/81/104/174":
                    self._send(PDF)
                else:
                    self._send(b"{}", 404)

            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fake.posts.append((self.path, json.loads(body)))
                self._send(json.dumps({"id": 1}).encode(), 201)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def zammad():
    fake = FakeZammad()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def client(zammad) -> ZammadClient:
    return ZammadClient(zammad.url, "tok")


@pytest.fixture
def config(zammad) -> ZammadConfig:
    return ZammadConfig(zammad.url, "tok", webhook_secret=SECRET, inbound_customer="fax@x.example")


@pytest.fixture
def webhook(service, client, config):
    server = make_server(("127.0.0.1", 0), service, client, config)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def post(payload: dict, secret: str | None = SECRET) -> tuple[int, dict]:
        body = json.dumps(payload).encode()
        headers = {}
        if secret is not None:
            digest = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
            headers["X-Hub-Signature"] = f"sha1={digest}"
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}{WEBHOOK_PATH}", data=body, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    yield post
    server.shutdown()
    server.server_close()


def payload(fax_number: str | None = "030 123456", article: dict = ARTICLE) -> dict:
    return {"ticket": {"id": 81, "number": "10081", "fax_number": fax_number}, "article": article}


def test_client_finds_ticket_and_downloads_pdf(client, zammad):
    ticket = client.find_ticket("10081")
    (attachment,) = client.pdf_attachments(ticket["id"])
    assert attachment.filename == "befund.pdf"
    assert client.download(attachment) == PDF
    assert zammad.tokens == {"Token token=tok"}


def test_client_reports_http_errors(client):
    with pytest.raises(FaxError):
        client.get_ticket(999)


def test_webhook_faxes_pdf_and_reports_result(webhook, service, client, config, zammad):
    status, body = webhook(payload())
    assert status == 200 and len(body["faxes"]) == 1
    (entry,) = service.journal.entries()
    assert (entry.number, entry.subject, entry.ticket_id) == ("030123456", "befund.pdf", 81)
    assert entry.document.read_bytes() == PDF
    assert zammad.posts == []

    bridge.sync(service, client, config)
    ((path, note),) = zammad.posts
    assert path == "/api/v1/ticket_articles"
    assert note["ticket_id"] == 81 and note["internal"] is True
    assert note["subject"] == "TeleSaurier: Fax gesendet"
    (report,) = note["attachments"]
    assert report["filename"] == f"sendebericht-{entry.id}.pdf"
    assert (
        "030123456" in PdfReader(BytesIO(base64.b64decode(report["data"]))).pages[0].extract_text()
    )
    assert service.journal.get(entry.id).user == "agent.anna"
    assert service.journal.get(entry.id).status is FaxStatus.SENT


def test_webhook_retry_does_not_fax_twice(webhook, service):
    webhook(payload())
    status, body = webhook(payload())
    assert status == 200 and body["faxes"] == []
    assert len(service.journal.entries()) == 1


@pytest.mark.parametrize("secret", [None, "wrong"])
def test_webhook_rejects_bad_signature(webhook, service, secret):
    status, _ = webhook(payload(), secret=secret)
    assert status == 403
    assert service.journal.entries() == []


def test_webhook_explains_missing_number_in_ticket(webhook, service, zammad):
    status, body = webhook(payload(fax_number=None))
    assert status == 200 and body["faxes"] == []
    assert service.journal.entries() == []
    ((_, note),) = zammad.posts
    assert note["subject"] == "TeleSaurier: Fax nicht versendet"
    assert "fax_number" in note["body"]


def test_webhook_ignores_own_notes(webhook, service, zammad):
    own = {"id": 105, "subject": "TeleSaurier: Fax nicht versendet", "attachments": []}
    status, body = webhook(payload(article=own))
    assert status == 200 and body["faxes"] == []
    assert zammad.posts == []


def test_webhook_rejects_malformed_payload(webhook):
    status, _ = webhook({"ticket": {}})
    assert status == 400


def test_inbound_fax_becomes_ticket(service, transport, client, config, zammad):
    (transport.inbox / "+4930987654.pdf").write_bytes(INBOUND)
    bridge.sync(service, client, config)
    ((path, ticket),) = zammad.posts
    assert path == "/api/v1/tickets"
    assert ticket["title"] == "Fax von +4930987654"
    assert ticket["customer"] == "fax@x.example"
    (attachment,) = ticket["article"]["attachments"]
    assert base64.b64decode(attachment["data"]) == INBOUND


def test_server_refuses_to_start_without_secret(service, client, zammad):
    with pytest.raises(ValueError):
        make_server(("127.0.0.1", 0), service, client, ZammadConfig(zammad.url, "tok"))
