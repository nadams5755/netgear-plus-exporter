"""Map a py-netgear-plus ``get_switch_infos()`` dict to Prometheus metric families.

Kept as a pure function of the dict (plus a couple of probe-level values the
dict doesn't carry) so it can be unit tested against fixture dicts without a
live switch or connector.
"""

from __future__ import annotations

import re
from typing import Any

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

_PORT_STATUS_KEY_RE = re.compile(r"^port_(\d+)_status$")

# The upstream library reports traffic in megabytes (already rounded), not
# raw bytes. We convert to bytes for Prometheus base-unit convention, which
# means these counters carry ~0.01 MB (~10 KB) of quantization noise -- this
# is a limitation of py-netgear-plus's public API, not something we can
# recover precision on.
_MB_TO_BYTES = 1_000_000


def _port_numbers(switch_data: dict[str, Any]) -> list[int]:
    numbers = [
        int(match.group(1))
        for key in switch_data
        if (match := _PORT_STATUS_KEY_RE.match(key))
    ]
    return sorted(numbers)


def build_metric_families(
    switch_data: dict[str, Any],
    *,
    target: str,
    up: bool,
    probe_duration_seconds: float,
) -> list[Metric]:
    """Build the list of metric families for a single /probe response."""
    up_family = GaugeMetricFamily(
        "netgear_plus_up", "Whether the last probe of this switch succeeded.", labels=["target"]
    )
    up_family.add_metric([target], 1.0 if up else 0.0)

    duration_family = GaugeMetricFamily(
        "netgear_plus_probe_duration_seconds",
        "How long the probe of this switch took, in seconds.",
        labels=["target"],
    )
    duration_family.add_metric([target], probe_duration_seconds)

    families = [up_family, duration_family]

    if not up:
        return families

    families.append(_switch_info_family(switch_data, target))

    rx_bytes = CounterMetricFamily(
        "netgear_plus_port_receive_bytes",
        "Cumulative bytes received on a port since switch boot.",
        labels=["target", "port"],
    )
    tx_bytes = CounterMetricFamily(
        "netgear_plus_port_transmit_bytes",
        "Cumulative bytes transmitted on a port since switch boot.",
        labels=["target", "port"],
    )
    rx_speed = GaugeMetricFamily(
        "netgear_plus_port_receive_speed_bytes",
        "Receive throughput on a port, in bytes/sec, as computed by py-netgear-plus"
        " over its polling interval.",
        labels=["target", "port"],
    )
    tx_speed = GaugeMetricFamily(
        "netgear_plus_port_transmit_speed_bytes",
        "Transmit throughput on a port, in bytes/sec, as computed by py-netgear-plus"
        " over its polling interval.",
        labels=["target", "port"],
    )
    crc_errors = CounterMetricFamily(
        "netgear_plus_port_crc_errors",
        "Cumulative CRC errors on a port since switch boot.",
        labels=["target", "port"],
    )
    link_up = GaugeMetricFamily(
        "netgear_plus_port_link_up", "Whether a port has an active link.", labels=["target", "port"]
    )
    link_speed = GaugeMetricFamily(
        "netgear_plus_port_link_speed_mbps",
        "Negotiated link speed of a port, in Mbps (0 if down/unknown).",
        labels=["target", "port"],
    )
    poe_status = GaugeMetricFamily(
        "netgear_plus_port_poe_status",
        "Whether PoE power is active on a port (PoE-capable models only).",
        labels=["target", "port"],
    )
    poe_power = GaugeMetricFamily(
        "netgear_plus_port_poe_power_watts",
        "PoE power draw on a port, in watts (PoE-capable models only).",
        labels=["target", "port"],
    )
    port_info = GaugeMetricFamily(
        "netgear_plus_port_info",
        "Port identity metadata; value is always 1. Only emitted for ports with a"
        " description configured on the switch.",
        labels=["target", "port", "description"],
    )

    for port_number in _port_numbers(switch_data):
        port = str(port_number)
        labels = [target, port]

        status = switch_data.get(f"port_{port_number}_status")
        if status is not None:
            link_up.add_metric(labels, 1.0 if status == "on" else 0.0)

        description = switch_data.get(f"port_{port_number}_description")
        if description is not None:
            port_info.add_metric([target, port, str(description)], 1.0)

        speed_mbps = switch_data.get(f"port_{port_number}_connection_speed")
        if speed_mbps is not None:
            link_speed.add_metric(labels, float(speed_mbps))

        sum_rx_mb = switch_data.get(f"port_{port_number}_sum_rx_mbytes")
        if sum_rx_mb is not None:
            rx_bytes.add_metric(labels, sum_rx_mb * _MB_TO_BYTES)

        sum_tx_mb = switch_data.get(f"port_{port_number}_sum_tx_mbytes")
        if sum_tx_mb is not None:
            tx_bytes.add_metric(labels, sum_tx_mb * _MB_TO_BYTES)

        speed_rx_mb = switch_data.get(f"port_{port_number}_speed_rx_mbytes")
        if speed_rx_mb is not None:
            rx_speed.add_metric(labels, speed_rx_mb * _MB_TO_BYTES)

        speed_tx_mb = switch_data.get(f"port_{port_number}_speed_tx_mbytes")
        if speed_tx_mb is not None:
            tx_speed.add_metric(labels, speed_tx_mb * _MB_TO_BYTES)

        crc = switch_data.get(f"port_{port_number}_crc_errors")
        if crc is not None:
            crc_errors.add_metric(labels, crc)

        poe_active = switch_data.get(f"port_{port_number}_poe_power_active")
        if poe_active is not None:
            poe_status.add_metric(labels, 1.0 if poe_active == "on" else 0.0)

        poe_watts = switch_data.get(f"port_{port_number}_poe_output_power")
        if poe_watts is not None:
            poe_power.add_metric(labels, float(poe_watts))

    families.extend(
        [
            link_up,
            link_speed,
            port_info,
            rx_bytes,
            tx_bytes,
            rx_speed,
            tx_speed,
            crc_errors,
            poe_status,
            poe_power,
        ]
    )
    return families


def _switch_info_family(switch_data: dict[str, Any], target: str) -> Metric:
    info = GaugeMetricFamily(
        "netgear_plus_switch_info",
        "Switch identity metadata; value is always 1.",
        labels=["target", "name", "serial_number", "bootloader", "firmware", "ip"],
    )
    info.add_metric(
        [
            target,
            str(switch_data.get("switch_name") or ""),
            str(switch_data.get("switch_serial_number") or ""),
            str(switch_data.get("switch_bootloader") or ""),
            str(switch_data.get("switch_firmware") or ""),
            str(switch_data.get("switch_ip") or ""),
        ],
        1.0,
    )
    return info
