"""Explicit, versioned entry-point loading. Plugins are trusted Python code."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, Protocol, runtime_checkable

API_VERSION = 1
ROLES = {"contracts", "storage", "catalogs", "queries", "lineage", "execution", "verification"}


@runtime_checkable
class ContractPlugin(Protocol):
    API_VERSION: int

    def load(self, path: str, object_name: str | None = None) -> Any: ...


@runtime_checkable
class LineagePlugin(Protocol):
    API_VERSION: int

    def ingest(self, source: Any) -> Any: ...


@runtime_checkable
class VerificationPlugin(Protocol):
    API_VERSION: int

    def verify(self, adapter: Any, plan: Any, baseline: dict) -> dict: ...


def available(role: str) -> tuple[str, ...]:
    if role not in ROLES:
        raise ValueError(f"Unknown plugin role: {role}")
    return tuple(sorted(ep.name for ep in entry_points(group=f"partitionguard.{role}")))


def load_plugin(role: str, name: str):
    if role not in ROLES:
        raise ValueError(f"Unknown plugin role: {role}")
    found = [ep for ep in entry_points(group=f"partitionguard.{role}") if ep.name == name]
    if len(found) != 1:
        raise ValueError(f"Expected one {role} plugin {name!r}; found {len(found)}")
    plugin = found[0].load()
    if getattr(plugin, "API_VERSION", None) != API_VERSION:
        raise ValueError(f"Plugin {name!r} must declare API_VERSION={API_VERSION}")
    if not callable(plugin):
        raise ValueError(f"Plugin {name!r} must export a callable factory")
    return plugin
