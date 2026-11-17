"""HTTP service: receives Zammad webhooks and syncs fax results back into tickets."""

import hashlib
import hmac
import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.config import Settings, ZammadConfig, build_service, load_settings
from src.core.service import FaxService
from src.zammad import bridge
from src.zammad.client import ZammadClient

log = logging.getLogger(__name__)
WEBHOOK_PATH = "/webhook/zammad"
MAX_BODY_BYTES = 5 * 1024 * 1024


def signature_valid(secret: str, body: bytes, header: str | None) -> bool:
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return hmac.compare_digest(expected, (header or "").removeprefix("sha1="))


def make_server(
    address: tuple[str, int], service: FaxService, client: ZammadClient, config: ZammadConfig
) -> ThreadingHTTPServer:
    if not config.webhook_secret:
        raise ValueError(
            "Kein Webhook-Geheimnis konfiguriert (zammad.webhook_secret). "
            "Ohne Signaturprüfung startet der Dienst nicht."
        )

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: HTTPStatus, data: dict) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - name required by http.server
            if self.path == "/health":
                self._reply(HTTPStatus.OK, {"status": "ok"})
            else:
                self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != WEBHOOK_PATH:
                self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too large"})
                return
            body = self.rfile.read(length)
            signature = self.headers.get("X-Hub-Signature")
            if not signature_valid(config.webhook_secret, body, signature):
                self._reply(HTTPStatus.FORBIDDEN, {"error": "invalid signature"})
                return
            try:
                payload = json.loads(body)
                entries = bridge.send_from_webhook(service, client, config, payload)
            except (ValueError, KeyError, TypeError):
                self._reply(HTTPStatus.BAD_REQUEST, {"error": "invalid payload"})
                return
            except Exception:
                log.exception("Webhook processing failed")
                self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error"})
                return
            self._reply(HTTPStatus.OK, {"faxes": [entry.id for entry in entries]})

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            log.info("%s %s", self.address_string(), format % args)

    return ThreadingHTTPServer(address, Handler)


def _sync_loop(
    service: FaxService,
    client: ZammadClient,
    config: ZammadConfig,
    seconds: int,
    stop: threading.Event,
) -> None:
    while not stop.wait(seconds):
        try:
            bridge.sync(service, client, config)
        except Exception:
            log.exception("Sync round failed")


def run(settings: Settings) -> None:
    if settings.zammad is None:
        raise ValueError("Für den Dienst muss Zammad konfiguriert sein ([zammad] url und token).")
    service = build_service(settings)
    client = ZammadClient(settings.zammad.url, settings.zammad.token)
    server = make_server(
        (settings.server_host, settings.server_port), service, client, settings.zammad
    )
    stop = threading.Event()
    threading.Thread(
        target=_sync_loop,
        args=(service, client, settings.zammad, settings.poll_seconds, stop),
        daemon=True,
    ).start()
    log.info("TeleSaurier service listening on %s:%s", settings.server_host, settings.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run(load_settings())
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
