from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter

from src.core.journal import Journal
from src.core.service import FaxService
from src.transports.simulated import SimulatedTransport


def make_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    path = tmp_path / "letter.pdf"
    path.write_bytes(make_pdf(pages=2))
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
