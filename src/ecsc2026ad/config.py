"""
Persisted host configuration for the ``ecsc2026ad`` CLI.

A "host" bundles a game's base URL with optional cache settings under a short name -- one free of URL
punctuation, so ``--host`` can take either a name or a URL. One host can be *selected*; CLI commands
then use its properties when no explicit ``--url`` is given. Stored as JSON at
``$ECSC_CONFIG_DIR/hosts.json`` (defaults to ``$XDG_CONFIG_HOME/ecsc2026ad`` or ``~/.config/ecsc2026ad``).

A built-in ``"default"`` host pointing at the ECSC 2026 production scoreboard is always available and
preselected until the player configures/selects something else, so the CLI works out of the box during
the competition. It only exists in memory unless a host command actually saves the store; a player can
override it with ``host add default <url>`` like any other host.
"""

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional

from ecsc2026ad.api import DEFAULT_API_URL

_URLISH = re.compile(r"[.:/\\@\s]")


def valid_host_name(name: str) -> bool:
    """
    Whether ``name`` may name a saved host.

    URL punctuation -- a period above all -- is rejected so that a ``--host`` argument is
    unambiguously one or the other: dotted means a domain or address, anything else means a saved
    name (see :func:`ecsc2026ad.api._assume_scheme`).
    """
    return bool(name) and not _URLISH.search(name)


def config_dir() -> Path:
    override = os.environ.get("ECSC_CONFIG_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "ecsc2026ad"


def config_path() -> Path:
    return config_dir() / "hosts.json"


@dataclass
class Host:
    name: str
    url: str
    lifetime: Optional[float] = None
    timeout: Optional[float] = None
    cache_dir: Optional[str] = None


DEFAULT_HOST_NAME = "default"
DEFAULT_HOST_URL = DEFAULT_API_URL


class HostStore:
    """Load/save the set of saved hosts and which one is selected."""

    def __init__(
        self, hosts: Optional[Dict[str, Host]] = None, selected: Optional[str] = None
    ) -> None:
        self.hosts: Dict[str, Host] = hosts or {}
        self.selected: Optional[str] = selected

    @classmethod
    def load(cls) -> "HostStore":
        try:
            data = json.loads(config_path().read_text("utf-8"))
        except (FileNotFoundError, ValueError):
            data = {}
        hosts: Dict[str, Host] = {}
        for name, props in data.get("hosts", {}).items():
            hosts[name] = Host(
                name=name,
                url=props.get("url", ""),
                lifetime=props.get("lifetime"),
                timeout=props.get("timeout"),
                cache_dir=props.get("cache_dir"),
            )
        selected = data.get("selected")
        if selected not in hosts:
            selected = None
        if DEFAULT_HOST_NAME not in hosts:
            hosts[DEFAULT_HOST_NAME] = Host(
                name=DEFAULT_HOST_NAME, url=DEFAULT_HOST_URL
            )
        return cls(hosts, selected)

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "selected": self.selected,
            "hosts": {
                name: {
                    k: v
                    for k, v in asdict(host).items()
                    if k != "name" and v is not None
                }
                for name, host in self.hosts.items()
            },
        }
        path.write_text(json.dumps(data, indent=2), "utf-8")

    def selected_host(self) -> Optional[Host]:
        """The host CLI commands use when no explicit ``--url``/``--host`` is given: the explicitly
        selected one, or else the built-in default host, so there's always something to fall back to."""
        name = self.selected if self.selected is not None else DEFAULT_HOST_NAME
        return self.hosts.get(name)
