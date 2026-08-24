from pathlib import Path

import pytest

from netgear_plus_exporter.config import ConfigError, UnknownModuleError, load_config


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "netgear_plus.yml"
    path.write_text(text)
    return path


def test_load_config_basic(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """
        modules:
          default:
            password: "secret"
          office:
            password: "secret2"
            model: GS308EPP
        """,
    )
    config = load_config(path)

    assert config.module_config("default").password == "secret"
    assert config.module_config("default").model is None
    assert config.module_config("office").model == "GS308EPP"


def test_module_config_defaults_to_default_module_when_none_given(tmp_path: Path) -> None:
    path = _write(tmp_path, 'modules:\n  default:\n    password: "secret"\n')
    config = load_config(path)

    assert config.module_config(None).password == "secret"
    assert config.module_config("").password == "secret"


def test_module_config_unknown_module_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, 'modules:\n  default:\n    password: "secret"\n')
    config = load_config(path)

    with pytest.raises(UnknownModuleError):
        config.module_config("nope")


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "does-not-exist.yml")


def test_load_config_malformed_yaml(tmp_path: Path) -> None:
    path = _write(tmp_path, "modules: [this, is, not, a, mapping")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_no_modules(tmp_path: Path) -> None:
    path = _write(tmp_path, "modules: {}\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_module_missing_password(tmp_path: Path) -> None:
    path = _write(tmp_path, "modules:\n  default:\n    {}\n")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_top_level_not_a_mapping(tmp_path: Path) -> None:
    path = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError):
        load_config(path)
