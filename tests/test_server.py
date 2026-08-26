import http.client
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import netgear_plus_exporter.connectors as connectors_module
from netgear_plus_exporter.config import ExporterConfig, ModuleConfig
from netgear_plus_exporter.server import create_server


class _FakeSwitchModel:
    MODEL_NAME = "GS308EPP"


class FakeConnector:
    """Stands in for py_netgear_plus.NetgearSwitchConnector in tests."""

    def __init__(self, target, switch_data=None, sleep_seconds=0.0, fail=False):
        self.target = target
        self._switch_data = switch_data or {"switch_ip": target, "port_1_status": "on"}
        self._sleep_seconds = sleep_seconds
        self._fail = fail
        self.login_calls = 0
        self.logout_calls = 0
        self.switch_model = _FakeSwitchModel

    def get_login_cookie(self):
        self.login_calls += 1
        return True

    def get_switch_infos(self):
        if self._sleep_seconds:
            time.sleep(self._sleep_seconds)
        if self._fail:
            raise RuntimeError("boom")
        return self._switch_data

    def delete_login_cookie(self):
        self.logout_calls += 1
        return True


@pytest.fixture
def make_server(monkeypatch):
    servers = []

    def _make(fake_connectors_by_target, *, modules=None, max_concurrent_requests=10):
        def fake_build_connector(target, module_config):
            return fake_connectors_by_target[target]

        monkeypatch.setattr(connectors_module, "_build_connector", fake_build_connector)

        config = ExporterConfig(modules=modules or {"default": ModuleConfig(password="pw")})
        server = create_server(
            "127.0.0.1", 0, config, max_concurrent_requests=max_concurrent_requests
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server

    yield _make

    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(server, path):
    conn = http.client.HTTPConnection(
        server.server_address[0], server.server_address[1], timeout=5
    )
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read().decode()
    finally:
        conn.close()


def test_probe_missing_target_returns_400(make_server):
    server = make_server({"t1": FakeConnector("t1")})
    status, _body = _get(server, "/probe")
    assert status == 400


def test_probe_unknown_module_returns_400(make_server):
    server = make_server({"t1": FakeConnector("t1")})
    status, _body = _get(server, "/probe?target=t1&module=nope")
    assert status == 400


def test_probe_success_returns_metrics(make_server):
    server = make_server({"t1": FakeConnector("t1")})
    status, body = _get(server, "/probe?target=t1")
    assert status == 200
    assert 'netgear_plus_up{target="t1"} 1.0' in body


def test_probe_success_logs_out_after_collecting(make_server):
    fake = FakeConnector("t1")
    server = make_server({"t1": fake})
    _get(server, "/probe?target=t1")
    assert fake.login_calls == 1
    assert fake.logout_calls == 1


def test_probe_failure_still_logs_out(make_server):
    fake = FakeConnector("t1", fail=True)
    server = make_server({"t1": fake})
    _get(server, "/probe?target=t1")
    assert fake.login_calls == 1
    assert fake.logout_calls == 1


def test_probe_logs_in_again_on_every_scrape(make_server):
    fake = FakeConnector("t1")
    server = make_server({"t1": fake})
    _get(server, "/probe?target=t1")
    _get(server, "/probe?target=t1")
    assert fake.login_calls == 2
    assert fake.logout_calls == 2


def test_probe_defaults_to_default_module(make_server):
    server = make_server(
        {"t1": FakeConnector("t1")}, modules={"default": ModuleConfig(password="pw")}
    )
    status, body = _get(server, "/probe?target=t1")
    assert status == 200
    assert 'netgear_plus_up{target="t1"} 1.0' in body


def test_probe_uses_named_module(make_server):
    server = make_server(
        {"t1": FakeConnector("t1")},
        modules={
            "default": ModuleConfig(password="pw"),
            "office": ModuleConfig(password="pw2"),
        },
    )
    status, body = _get(server, "/probe?target=t1&module=office")
    assert status == 200
    assert 'netgear_plus_up{target="t1"} 1.0' in body


def test_probe_failure_returns_up_zero(make_server):
    server = make_server({"t1": FakeConnector("t1", fail=True)})
    status, body = _get(server, "/probe?target=t1")
    assert status == 200
    assert 'netgear_plus_up{target="t1"} 0.0' in body


def test_metrics_endpoint_serves_self_metrics(make_server):
    server = make_server({})
    status, body = _get(server, "/metrics")
    assert status == 200
    assert "netgear_plus_exporter_probes_total" in body


def test_concurrent_probes_same_target_second_gets_503(make_server):
    server = make_server(
        {"t1": FakeConnector("t1", sleep_seconds=0.6)}, max_concurrent_requests=10
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_get, server, "/probe?target=t1")
        time.sleep(0.2)  # let the first request acquire the target lock
        second = executor.submit(_get, server, "/probe?target=t1")
        status1, _ = first.result(timeout=5)
        status2, _ = second.result(timeout=5)

    assert status1 == 200
    assert status2 == 503


def test_concurrent_probes_different_targets_run_in_parallel(make_server):
    server = make_server(
        {
            "t1": FakeConnector("t1", sleep_seconds=0.4),
            "t2": FakeConnector("t2", sleep_seconds=0.4),
        },
        max_concurrent_requests=10,
    )

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_get, server, "/probe?target=t1")
        second = executor.submit(_get, server, "/probe?target=t2")
        status1, _ = first.result(timeout=5)
        status2, _ = second.result(timeout=5)
    elapsed = time.perf_counter() - start

    assert status1 == 200
    assert status2 == 200
    # Serialized would take ~0.8s; parallel execution should stay well under that.
    assert elapsed < 0.75
