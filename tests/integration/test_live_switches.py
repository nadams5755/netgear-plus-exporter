"""Integration tests against a live netgear-plus-exporter process and real switches.

These are opt-in only -- excluded from the default `pytest`/`make test` run via the
`integration` marker (see pyproject.toml) -- because they require:

- netgear-plus-exporter already running and reachable (NETGEAR_PLUS_EXPORTER_URL,
  default http://localhost:9493 -- the exporter's default port)
- real NETGEAR Plus switches configured in its netgear_plus.yml and reachable at
  NETGEAR_PLUS_INTEGRATION_TARGETS

Run with `make test-integration`, which forces serial execution (no pytest-xdist):
concurrent probes of the *same* target would race against the exporter's own
non-blocking single-flight lock and spuriously see 503s.

Configure targets as a comma-separated `host[:module]` list, e.g.:

    NETGEAR_PLUS_INTEGRATION_TARGETS="10.11.12.2,10.11.12.3,192.168.88.5:office" \
        make test-integration

A target with no `:module` suffix uses the `default` module.
"""

from __future__ import annotations

import os
import time
import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.integration

EXPORTER_URL = os.environ.get("NETGEAR_PLUS_EXPORTER_URL", "http://localhost:9493")
# Probes can legitimately take tens of seconds against real hardware -- see the
# "Slow switches" section of the README -- so give requests plenty of room.
REQUEST_TIMEOUT_SECONDS = 120

_DEFAULT_TARGETS = "10.11.12.2,10.11.12.3,192.168.88.5"


def _parse_targets(raw: str) -> list[tuple[str, str]]:
    targets = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        host, _, module = entry.partition(":")
        targets.append((host, module or "default"))
    return targets


TARGETS = _parse_targets(os.environ.get("NETGEAR_PLUS_INTEGRATION_TARGETS", _DEFAULT_TARGETS))


def _get(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(  # noqa: S310 (local/LAN test target, not user input)
            f"{EXPORTER_URL}{path}", timeout=REQUEST_TIMEOUT_SECONDS
        ) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _sample_value(body: str, metric: str, *, target: str, port: str | None = None) -> float:
    labels = f'target="{target}"' if port is None else f'port="{port}",target="{target}"'
    prefix = f"{metric}{{{labels}}} "
    for line in body.splitlines():
        if line.startswith(prefix):
            return float(line[len(prefix) :])
    raise AssertionError(f"metric sample {prefix!r} not found in probe response:\n{body}")


def _probe(target: str, module: str, *, attempts: int = 3, retry_delay_seconds: float = 5) -> str:
    """Probe a target, retrying on transient failure (up=0) or a non-200 status.

    Real switches are known to be slow and occasionally miss the exporter's
    per-request timeout (see the README's "Slow switches" section), so a
    single failed probe isn't on its own evidence of a bug -- only a
    persistent one across several attempts is.
    """
    last_failure = ""
    for attempt in range(1, attempts + 1):
        status, body = _get(f"/probe?target={target}&module={module}")
        if status == 200:
            up = _sample_value(body, "netgear_plus_up", target=target)
            if up == 1.0:
                return body
            last_failure = f"probe reported netgear_plus_up=0 (attempt {attempt}/{attempts})"
        else:
            last_failure = f"HTTP {status} (attempt {attempt}/{attempts}):\n{body}"
        if attempt < attempts:
            time.sleep(retry_delay_seconds)
    raise AssertionError(
        f"probe of target={target!r} module={module!r} never succeeded after "
        f"{attempts} attempts; last failure: {last_failure}"
    )


@pytest.fixture(scope="module", autouse=True)
def _require_live_exporter() -> None:
    try:
        status, _body = _get("/metrics")
    except OSError as exc:
        pytest.skip(f"netgear-plus-exporter not reachable at {EXPORTER_URL}: {exc}")
    if status != 200:
        pytest.skip(f"netgear-plus-exporter at {EXPORTER_URL} returned {status} for /metrics")


@pytest.mark.parametrize("target,module", TARGETS, ids=[t for t, _ in TARGETS])
def test_probe_live_switch_succeeds(target: str, module: str) -> None:
    body = _probe(target, module)
    assert "netgear_plus_switch_info{" in body
    # Every switch has at least a port 1.
    link_status = _sample_value(body, "netgear_plus_port_link_up", target=target, port="1")
    assert link_status in (0.0, 1.0)


@pytest.mark.parametrize("target,module", TARGETS, ids=[t for t, _ in TARGETS])
def test_probe_receive_counter_is_monotonic_across_two_probes(target: str, module: str) -> None:
    first_body = _probe(target, module)
    time.sleep(2)
    second_body = _probe(target, module)

    first = _sample_value(
        first_body, "netgear_plus_port_receive_bytes_total", target=target, port="1"
    )
    second = _sample_value(
        second_body, "netgear_plus_port_receive_bytes_total", target=target, port="1"
    )
    assert second >= first
