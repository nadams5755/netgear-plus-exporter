"""Exporter configuration: named credential "modules", analogous to snmp_exporter's snmp.yml.

Mirrors the snmp_exporter split of responsibilities: this file defines a
small number of reusable auth profiles (modules), not one entry per switch.
The actual inventory of switches -- and which module each one uses -- lives
in prometheus.yml's scrape config, via the `target` and `module` /probe
query params. This avoids maintaining the same switch list in two places.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

DEFAULT_MODULE = "default"


class ConfigError(Exception):
    """Raised for invalid or unreadable exporter configuration."""


class UnknownModuleError(Exception):
    """Raised when a probed module has no matching entry in the config file."""


@dataclasses.dataclass(frozen=True)
class ModuleConfig:
    password: str
    model: str | None = None


@dataclasses.dataclass(frozen=True)
class ExporterConfig:
    modules: dict[str, ModuleConfig]

    def module_config(self, module: str | None) -> ModuleConfig:
        """Look up a module by name, falling back to DEFAULT_MODULE if None."""
        name = module or DEFAULT_MODULE
        try:
            return self.modules[name]
        except KeyError:
            raise UnknownModuleError(name) from None


def load_config(path: str | Path) -> ExporterConfig:
    """Load and validate the exporter's YAML config file.

    Format:
        modules:
          default:
            password: "secret"
          office:
            password: "secret2"
            model: GS308EPP   # optional, skips autodetection

    prometheus.yml then lists actual switch targets and, per target group,
    which module's credentials to use (see README for a full example).
    """
    config_path = Path(path)
    try:
        raw_text = config_path.read_text()
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {config_path}: {exc}") from exc

    try:
        raw: Any = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Config file {config_path} must contain a mapping at the top level")

    raw_modules = raw.get("modules") or {}
    if not isinstance(raw_modules, dict):
        raise ConfigError("'modules' must be a mapping of module name -> module config")

    modules: dict[str, ModuleConfig] = {}
    for name, entry in raw_modules.items():
        entry = entry or {}
        if not isinstance(entry, dict):
            raise ConfigError(f"Module {name!r} config must be a mapping")
        password = entry.get("password")
        if not password:
            raise ConfigError(f"Module {name!r} has no 'password'")
        model = entry.get("model")
        modules[str(name)] = ModuleConfig(password=password, model=model)

    if not modules:
        raise ConfigError(f"Config file {config_path} defines no modules")

    return ExporterConfig(modules=modules)
