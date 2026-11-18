"""Fax routes and the registry that builds them from configuration.

To add a route, write a class implementing FaxSender and/or FaxReceiver (see
base.py) and register it below. ``options`` is the route's own config section,
e.g. ``[fax.fritzbox]``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.transports.base import FaxReceiver, FaxSender
from src.transports.simulated import SimulatedTransport

SIMULATED = "simulated"
Options = dict[str, Any]


@dataclass(frozen=True)
class Route:
    label: str  # shown in the user interface (German)
    build: Callable[[Options, Path], Any]


def _simulated(options: Options, data_dir: Path) -> SimulatedTransport:
    return SimulatedTransport(Path(options.get("dir", data_dir / "simulated")).expanduser())


SENDERS: dict[str, Route] = {
    SIMULATED: Route("Testmodus", _simulated),
}
RECEIVERS: dict[str, Route] = {
    SIMULATED: Route("Testmodus", _simulated),
}


def _build(
    kind: str, registry: dict[str, Route], names: tuple[str, ...], options: Options, data_dir: Path
) -> dict[str, Any]:
    unknown = [name for name in names if name not in registry]
    if unknown:
        raise ValueError(
            f"Unbekannter Fax-Weg zum {kind}: {', '.join(unknown)}. "
            f"Verfügbar: {', '.join(registry)}"
        )
    return {name: registry[name].build(options.get(name, {}), data_dir) for name in names}


def build_senders(names: tuple[str, ...], options: Options, data_dir: Path) -> dict[str, FaxSender]:
    return _build("Senden", SENDERS, names, options, data_dir)


def build_receivers(
    names: tuple[str, ...], options: Options, data_dir: Path
) -> dict[str, FaxReceiver]:
    return _build("Empfangen", RECEIVERS, names, options, data_dir)


def route_label(name: str) -> str:
    route = SENDERS.get(name) or RECEIVERS.get(name)
    return route.label if route else name
