import getpass
import sqlite3
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from pypdf import PdfReader

from src.config import build_service, load_settings
from src.core.journal import Journal
from src.core.models import Direction, FaxError, FaxJob, FaxStatus, normalize_fax_number
from src.core.service import FaxService
from src.transports.simulated import SimulatedTransport

INBOUND = b"%PDF-1.4 inbound"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("030 / 123-456", "030123456"),
        ("+49 (30) 123456", "+4930123456"),
        (" 0301.2345 ", "03012345"),
    ],
)
def test_normalize_fax_number(raw, expected):
    assert normalize_fax_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "12", "abc", "030+123", "030 12 34x"])
def test_normalize_fax_number_rejects_invalid(raw):
    with pytest.raises(FaxError):
        normalize_fax_number(raw)


def test_send_then_refresh_marks_sent(service, transport, pdf):
    entry = service.send(FaxJob("030 123456", pdf, "Brief", ticket_id=7))
    assert entry.status is FaxStatus.SENDING
    assert (entry.pages, entry.attempts, entry.user) == (2, 1, getpass.getuser())
    assert entry.number == "030123456"
    assert entry.document != pdf and entry.document.read_bytes() == pdf.read_bytes()
    assert (transport.outbox / f"{entry.remote_id}.pdf").exists()

    (changed,) = service.refresh()
    assert changed.status is FaxStatus.SENT
    assert changed.ticket_id == 7
    assert service.refresh() == []


def test_failed_transmission_is_reported(service, pdf):
    service.send(FaxJob("0301230000", pdf))
    (changed,) = service.refresh()
    assert changed.status is FaxStatus.FAILED
    assert changed.detail


def test_transport_error_ends_up_in_journal(service, transport, pdf, monkeypatch):
    def broken(job):
        raise RuntimeError("line down")

    monkeypatch.setattr(transport, "send", broken)
    entry = service.send(FaxJob("030123456", pdf))
    assert entry.status is FaxStatus.FAILED
    assert "line down" in entry.detail


def test_send_rejects_bad_input(service, pdf, tmp_path):
    text = tmp_path / "notes.pdf"
    text.write_text("not a pdf")
    with pytest.raises(FaxError):
        service.send(FaxJob("nope", pdf))
    with pytest.raises(FaxError):
        service.send(FaxJob("030123456", text))
    with pytest.raises(FaxError):
        service.send(FaxJob("030123456", tmp_path / "missing.pdf"))
    assert service.journal.entries() == []


def test_inbound_fax_is_collected_once(service, transport):
    (transport.inbox / "+4930987654.pdf").write_bytes(INBOUND)
    (entry,) = service.poll_inbound()
    assert entry.direction is Direction.INBOUND
    assert entry.status is FaxStatus.RECEIVED
    assert entry.number == "+4930987654"
    assert entry.document.read_bytes() == INBOUND
    assert service.poll_inbound() == []
    assert len(service.journal.entries()) == 1


def test_load_settings(tmp_path, monkeypatch):
    assert load_settings(tmp_path / "missing.toml").zammad is None

    config = tmp_path / "config.toml"
    config.write_text(
        '[general]\ndata_dir = "data"\n[zammad]\nurl = "https://z.example"\ntoken = "file"\n'
        "[server]\nport = 9000\n"
    )
    monkeypatch.setenv("TELESAURIER_ZAMMAD_TOKEN", "env")
    settings = load_settings(config)
    assert settings.zammad.token == "env"
    assert settings.zammad.fax_number_field == "fax_number"
    assert settings.server_port == 9000
    assert settings.send_routes == ("simulated",)


def test_routes_come_from_config(tmp_path, pdf):
    config = tmp_path / "config.toml"
    config.write_text(
        f'[general]\ndata_dir = "{(tmp_path / "data").as_posix()}"\n'
        '[fax]\nsend = ["simulated"]\nreceive = []\nmax_attempts = 5\nretry_pause_seconds = 10\n'
        f'[fax.simulated]\ndir = "{(tmp_path / "sim").as_posix()}"\n'
    )
    service = build_service(load_settings(config))
    entry = service.send(FaxJob("030123456", pdf))
    assert entry.route == "simulated"
    assert (tmp_path / "sim" / "outbox" / f"{entry.remote_id}.pdf").exists()
    (tmp_path / "sim" / "inbox" / "030.pdf").write_bytes(b"%PDF-1.4")
    assert (service._max_attempts, service._retry_pause) == (5, timedelta(seconds=10))
    assert service.poll_inbound() == []  # no receive route configured


def test_unknown_route_in_config_is_rejected(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[fax]\nsend = ["carrier-pigeon"]\n')
    with pytest.raises(ValueError, match="carrier-pigeon"):
        build_service(load_settings(config))


def test_send_uses_chosen_route_and_default(tmp_path, pdf, transport):
    other = SimulatedTransport(tmp_path / "other")
    service = FaxService(
        Journal(tmp_path / "j.sqlite"), {"a": transport, "b": other}, {}, tmp_path / "docs"
    )
    assert service.send_routes == ["a", "b"]
    default = service.send(FaxJob("030123456", pdf))
    chosen = service.send(FaxJob("030123456", pdf, route="b"))
    assert (default.route, chosen.route) == ("a", "b")
    assert (other.outbox / f"{chosen.remote_id}.pdf").exists()
    assert {entry.status for entry in service.refresh()} == {FaxStatus.SENT}
    with pytest.raises(FaxError):
        service.send(FaxJob("030123456", pdf, route="nope"))


def test_journal_from_older_version_is_migrated(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE faxes (id INTEGER PRIMARY KEY AUTOINCREMENT, direction TEXT NOT NULL, "
            "number TEXT NOT NULL, document TEXT NOT NULL, subject TEXT NOT NULL DEFAULT '', "
            "ticket_id INTEGER, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', "
            "remote_id TEXT, origin TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO faxes (direction, number, document, status, created_at, updated_at) "
            "VALUES ('outbound', '030', 'x.pdf', 'sent', '2026-11-17T22:00:00+00:00', "
            "'2026-11-17T22:00:00+00:00')"
        )
    conn.close()
    (entry,) = Journal(path).entries()
    assert (entry.route, entry.user, entry.pages, entry.attempts) == ("", "", 0, 0)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 18, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def retrying(tmp_path, transport, clock) -> FaxService:
    return FaxService(
        Journal(tmp_path / "retry.sqlite"),
        {"simulated": transport},
        {},
        tmp_path / "retry-docs",
        max_attempts=3,
        retry_pause=timedelta(minutes=2),
        clock=clock,
    )


def test_busy_number_is_retried_after_the_pause(retrying, clock, pdf):
    retrying.send(FaxJob("0301230002", pdf))  # busy once, then goes through

    (waiting,) = retrying.refresh()
    assert waiting.status is FaxStatus.QUEUED
    assert "besetzt" in waiting.detail and "Versuch 2 von 3" in waiting.detail
    assert retrying.refresh() == []  # pause not over yet

    clock.now += timedelta(minutes=2)
    (resent,) = retrying.refresh()
    assert (resent.status, resent.attempts) == (FaxStatus.SENDING, 2)
    (done,) = retrying.refresh()
    assert done.status is FaxStatus.SENT


def test_always_busy_number_fails_after_max_attempts(retrying, clock, pdf):
    entry = retrying.send(FaxJob("0301230001", pdf))
    statuses = []
    for _ in range(10):
        statuses += [changed.status for changed in retrying.refresh()]
        clock.now += timedelta(minutes=2)
    final = retrying.journal.get(entry.id)
    assert (final.status, final.attempts) == (FaxStatus.FAILED, 3)
    assert statuses.count(FaxStatus.FAILED) == 1  # reported exactly once
    assert retrying.journal.unfinished() == []


def test_permanent_failure_is_not_retried(retrying, pdf):
    entry = retrying.send(FaxJob("0301230000", pdf))
    (failed,) = retrying.refresh()
    assert (failed.status, failed.attempts) == (FaxStatus.FAILED, 1)
    assert retrying.journal.get(entry.id).next_attempt_at is None


def test_report_lists_the_facts_of_a_finished_fax(service, pdf):
    entry = service.send(FaxJob("030 123456", pdf, "Befund Müller", ticket_id=81, user="anna"))
    with pytest.raises(FaxError):
        service.report(entry)  # still sending
    (done,) = service.refresh()
    text = PdfReader(BytesIO(service.report(done))).pages[0].extract_text()
    for expected in ("Sendebericht", "Gesendet", "030123456", "Befund Müller", "anna", "Testmodus"):
        assert expected in text
