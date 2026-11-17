import pytest

from src.config import load_settings
from src.core.models import Direction, FaxError, FaxJob, FaxStatus, normalize_fax_number


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
    (transport.inbox / "+4930987654.pdf").write_bytes(b"%PDF-1.4 inbound")
    (entry,) = service.poll_inbound()
    assert entry.direction is Direction.INBOUND
    assert entry.status is FaxStatus.RECEIVED
    assert entry.number == "+4930987654"
    assert entry.document.read_bytes() == b"%PDF-1.4 inbound"
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
    assert settings.transport == "simulated"
