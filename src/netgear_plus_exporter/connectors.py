"""Process-wide cache of NetgearSwitchConnector instances, one per target host.

Reusing a connector across probes matters for correctness, not just
efficiency: py-netgear-plus computes per-port rate/speed fields as deltas
against state stored on the connector instance (``_previous_data``). A fresh
connector per scrape would make every rate compute against no prior sample.
Reusing the connector also keeps the login session cookie warm.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from py_netgear_plus import NetgearSwitchConnector
from py_netgear_plus.models import MODELS

from .config import ModuleConfig

_MODELS_BY_NAME = {model_cls.MODEL_NAME: model_cls for model_cls in MODELS}


class UnknownModelError(Exception):
    """Raised when a config-specified model override doesn't match any known model."""


class TargetBusyError(Exception):
    """Raised when a probe for this target is already in flight.

    NETGEAR Plus switches have slow, single-session embedded web servers, so
    a second concurrent probe against the same switch (e.g. a Prometheus
    retry that overlaps a still-running probe) must fail fast rather than
    queue up and block a worker thread.
    """


class _CacheEntry:
    __slots__ = ("connector", "lock")

    def __init__(self, connector: NetgearSwitchConnector) -> None:
        self.connector = connector
        self.lock = threading.Lock()


def _build_connector(target: str, module_config: ModuleConfig) -> NetgearSwitchConnector:
    connector = NetgearSwitchConnector(target, module_config.password)
    if module_config.model:
        model_cls = _MODELS_BY_NAME.get(module_config.model)
        if model_cls is None:
            raise UnknownModelError(module_config.model)
        connector._set_instance_attributes_by_model(model_cls)  # noqa: SLF001
    return connector


class ConnectorPool:
    """Process-wide cache of connectors, keyed by target host.

    The module (credentials) used to build a target's connector is whichever
    one was passed in on that target's *first* probe; a later probe of the
    same target with a different module is a configuration inconsistency and
    is ignored rather than reconnecting, since the cached connector already
    holds a valid session.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}
        self._entries_lock = threading.Lock()

    def _get_or_create_entry(self, target: str, module_config: ModuleConfig) -> _CacheEntry:
        with self._entries_lock:
            entry = self._entries.get(target)
            if entry is None:
                entry = _CacheEntry(_build_connector(target, module_config))
                self._entries[target] = entry
            return entry

    @contextmanager
    def acquire(
        self, target: str, module_config: ModuleConfig
    ) -> Iterator[NetgearSwitchConnector]:
        """Acquire the cached connector for a target, without blocking.

        Raises TargetBusyError immediately if a probe for this target is
        already in flight, instead of waiting for it to finish.
        """
        entry = self._get_or_create_entry(target, module_config)
        if not entry.lock.acquire(blocking=False):
            raise TargetBusyError(target)
        try:
            yield entry.connector
        finally:
            entry.lock.release()

    def drop(self, target: str) -> None:
        """Evict a cached connector so the next probe starts a fresh session.

        Call this after a probe failure (auth failure, parse error, timeout)
        so a stale/corrupt session or login cookie doesn't poison future
        probes of the same target.
        """
        with self._entries_lock:
            self._entries.pop(target, None)
