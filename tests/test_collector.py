from prometheus_client import generate_latest
from prometheus_client.core import CollectorRegistry

from netgear_plus_exporter.collector import build_metric_families

FIXTURE = {
    "switch_ip": "192.168.1.5",
    "switch_name": "switch1",
    "switch_serial_number": "SN123",
    "switch_bootloader": "BL1",
    "switch_firmware": "FW1",
    "port_1_status": "on",
    "port_1_connection_speed": 1000,
    "port_1_sum_rx_mbytes": 12.34,
    "port_1_sum_tx_mbytes": 5.0,
    "port_1_speed_rx_mbytes": 0.01,
    "port_1_speed_tx_mbytes": 0.0,
    "port_1_poe_power_active": "on",
    "port_1_poe_output_power": 4.5,
    "port_2_status": "off",
    "port_2_connection_speed": 0,
    "port_2_crc_errors": 3,
}


class _StaticCollector:
    def __init__(self, families):
        self._families = families

    def collect(self):
        yield from self._families


def _render(families) -> str:
    registry = CollectorRegistry()
    registry.register(_StaticCollector(families))
    return generate_latest(registry).decode()


def test_up_and_duration_always_present() -> None:
    families = build_metric_families({}, target="t1", up=False, probe_duration_seconds=0.5)
    text = _render(families)
    assert 'netgear_plus_up{target="t1"} 0.0' in text
    assert 'netgear_plus_probe_duration_seconds{target="t1"} 0.5' in text


def test_down_probe_emits_no_switch_or_port_metrics() -> None:
    families = build_metric_families(FIXTURE, target="t1", up=False, probe_duration_seconds=0.1)
    text = _render(families)
    assert "netgear_plus_switch_info" not in text
    assert "netgear_plus_port_link_up" not in text


def test_successful_probe_maps_expected_fields() -> None:
    families = build_metric_families(FIXTURE, target="t1", up=True, probe_duration_seconds=1.23)
    text = _render(families)

    assert 'netgear_plus_up{target="t1"} 1.0' in text
    assert 'netgear_plus_port_link_up{port="1",target="t1"} 1.0' in text
    assert 'netgear_plus_port_link_up{port="2",target="t1"} 0.0' in text
    assert 'netgear_plus_port_link_speed_mbps{port="1",target="t1"} 1000.0' in text
    # 12.34 MB -> bytes
    assert 'netgear_plus_port_receive_bytes_total{port="1",target="t1"} 1.234e+07' in text
    assert 'netgear_plus_port_poe_status{port="1",target="t1"} 1.0' in text
    assert 'netgear_plus_port_poe_power_watts{port="1",target="t1"} 4.5' in text
    # crc_errors only present for port 2 in the fixture (mirrors the known
    # upstream last-port-only behavior); must not be fabricated for port 1.
    crc_lines = [
        line
        for line in text.splitlines()
        if line.startswith("netgear_plus_port_crc_errors_total{")
    ]
    assert crc_lines == ['netgear_plus_port_crc_errors_total{port="2",target="t1"} 3.0']


def test_missing_poe_fields_emit_no_poe_metrics() -> None:
    fixture = {"port_1_status": "on", "port_1_connection_speed": 1000}
    families = build_metric_families(fixture, target="t1", up=True, probe_duration_seconds=0.1)
    text = _render(families)
    # HELP/TYPE headers are emitted even for metric families with zero
    # samples; what matters is that no actual sample line was produced.
    assert "netgear_plus_port_poe_status{" not in text
    assert "netgear_plus_port_poe_power_watts{" not in text
