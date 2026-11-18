from pathlib import Path

import pytest

from src.core.journal import Journal
from src.core.service import FaxService
from src.transports.simulated import SimulatedTransport


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "letter.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    return path


@pytest.fixture
def transport(tmp_path: Path) -> SimulatedTransport:
    return SimulatedTransport(tmp_path / "simulated")


@pytest.fixture
def service(tmp_path: Path, transport: SimulatedTransport) -> FaxService:
    return FaxService(
        Journal(tmp_path / "journal.sqlite"),
        {"simulated": transport},
        {"simulated": transport},
        tmp_path / "documents",
    )
