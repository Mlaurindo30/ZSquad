"""Alias re-export for HostRegistry (Milestone R11)."""

from scripts.runtime.dispatch.host_registry import (
    AdapterFactory,
    HostRegistry,
    create_default_registry,
    default_host_registry,
)

__all__ = [
    "AdapterFactory",
    "HostRegistry",
    "create_default_registry",
    "default_host_registry",
]
