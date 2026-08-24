import subprocess
import sys

from netgear_plus_exporter.__main__ import MIN_PYTHON, _python_version_error


def test_python_version_error_below_minimum() -> None:
    error = _python_version_error((3, 9, 5))
    assert error is not None
    assert "3.10" in error
    assert "3.9.5" in error


def test_python_version_error_at_or_above_minimum() -> None:
    assert _python_version_error(MIN_PYTHON + (0,)) is None
    assert _python_version_error((3, 13, 1)) is None


def test_import_exits_cleanly_under_unsupported_python() -> None:
    """End-to-end: importing __main__ under a too-old interpreter exits(1)
    with a clear stderr message, rather than failing on newer syntax deeper
    in the package (e.g. `X | Y` type hints in config.py/server.py)."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.version_info = (3, 9, 0); import netgear_plus_exporter.__main__",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1
    assert "requires Python 3.10+" in result.stderr
    assert "found 3.9.0" in result.stderr
