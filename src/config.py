"""Settings from a TOML file; see config.example.toml."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from src.core.journal import Journal
from src.core.service import FaxService
from src.transports import SIMULATED, build_transport

CONFIG_ENV = "TELESAURIER_CONFIG"
TOKEN_ENV = "TELESAURIER_ZAMMAD_TOKEN"
SECRET_ENV = "TELESAURIER_WEBHOOK_SECRET"
DEFAULT_DIR = Path.home() / ".telesaurier"


@dataclass(frozen=True)
class ZammadConfig:
    url: str
    token: str
    fax_number_field: str = "fax_number"
    inbound_group: str = "Users"
    inbound_customer: str = ""
    webhook_secret: str = ""


@dataclass(frozen=True)
class Settings:
    data_dir: Path = DEFAULT_DIR
    transport: str = SIMULATED
    server_host: str = "127.0.0.1"
    server_port: int = 8520
    poll_seconds: int = 30
    zammad: ZammadConfig | None = None


def load_settings(path: Path | None = None) -> Settings:
    """Read the config file; a missing file yields defaults (simulated, no Zammad)."""
    path = path or Path(os.environ.get(CONFIG_ENV, DEFAULT_DIR / "config.toml"))
    raw = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    general = raw.get("general", {})
    server = raw.get("server", {})
    zammad_raw = dict(raw.get("zammad", {}))
    zammad = None
    if zammad_raw.get("url"):
        zammad_raw["token"] = os.environ.get(TOKEN_ENV, zammad_raw.get("token", ""))
        zammad_raw["webhook_secret"] = os.environ.get(
            SECRET_ENV, zammad_raw.get("webhook_secret", "")
        )
        zammad = ZammadConfig(**zammad_raw)
    return Settings(
        data_dir=Path(general.get("data_dir", DEFAULT_DIR)).expanduser(),
        transport=general.get("transport", SIMULATED),
        server_host=server.get("host", "127.0.0.1"),
        server_port=server.get("port", 8520),
        poll_seconds=server.get("poll_seconds", 30),
        zammad=zammad,
    )


def build_service(settings: Settings) -> FaxService:
    return FaxService(
        Journal(settings.data_dir / "journal.sqlite"),
        build_transport(settings.transport, settings.data_dir),
        settings.data_dir / "documents",
    )
