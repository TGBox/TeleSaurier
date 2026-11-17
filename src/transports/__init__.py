"""Fax backends."""

from pathlib import Path

from src.transports.base import FaxTransport
from src.transports.simulated import SimulatedTransport

SIMULATED = "simulated"


def build_transport(name: str, data_dir: Path) -> FaxTransport:
    if name == SIMULATED:
        return SimulatedTransport(data_dir / "simulated")
    raise ValueError(f"Unbekannter Fax-Weg in der Konfiguration: „{name}“")
