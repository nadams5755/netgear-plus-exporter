"""HTTP server exposing the snmp_exporter-style /probe endpoint.

Concurrency model: incoming connections are dispatched to a *bounded*
ThreadPoolExecutor (unlike stock ThreadingHTTPServer, which spawns an
unbounded thread per connection) so a burst of scrapes can't exhaust
resources. Within a single connection, a probe against a given target
acquires that target's lock via ConnectorPool.acquire() without blocking; if
another probe of the same target is already in flight, the request fails
fast with 503 rather than queuing (NETGEAR Plus switches are slow and appear
to support only one session at a time, so queuing would just stack up
workers behind a slow device).
"""

from __future__ import annotations

import http.server
import logging
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, generate_latest
from prometheus_client.core import CollectorRegistry

from .collector import build_metric_families
from .config import ExporterConfig, UnknownModuleError
from .connectors import ConnectorPool, TargetBusyError

_LOGGER = logging.getLogger(__name__)

PROBES_TOTAL = Counter(
    "netgear_plus_exporter_probes_total",
    "Total number of /probe requests handled, by result.",
    ["result"],
)

_INDEX_BODY = b"""<html>
<head><title>netgear-plus-exporter</title></head>
<body>
<h1>netgear-plus-exporter</h1>
<p><a href="/probe?target=192.168.1.5&amp;module=default">
/probe?target=192.168.1.5&amp;module=default</a></p>
<p><a href="/metrics">/metrics</a> (exporter self-metrics)</p>
</body>
</html>
"""


class _StaticCollector:
    def __init__(self, families):
        self._families = families

    def collect(self):
        yield from self._families


def _render_probe_metrics(families) -> bytes:
    registry = CollectorRegistry()
    registry.register(_StaticCollector(families))
    return generate_latest(registry)


class _RequestHandler(http.server.BaseHTTPRequestHandler):
    server: _ExporterHTTPServer

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        _LOGGER.debug("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/probe":
            self._handle_probe(parsed)
        elif parsed.path == "/metrics":
            self._handle_self_metrics()
        elif parsed.path == "/":
            self._respond(200, _INDEX_BODY, content_type="text/html")
        else:
            self._respond(404, b"not found\n")

    def _handle_self_metrics(self) -> None:
        self._respond(200, generate_latest(REGISTRY), content_type=CONTENT_TYPE_LATEST)

    def _handle_probe(self, parsed: urllib.parse.ParseResult) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        target_values = query.get("target")
        if not target_values or not target_values[0]:
            self._respond(400, b"missing required 'target' query parameter\n")
            return
        target = target_values[0]
        module_values = query.get("module")
        module = module_values[0] if module_values else None

        try:
            module_config = self.server.config.module_config(module)
        except UnknownModuleError as exc:
            PROBES_TOTAL.labels(result="unknown_module").inc()
            self._respond(400, f"unknown module {str(exc)!r}\n".encode())
            return

        start = time.perf_counter()
        try:
            with self.server.pool.acquire(target, module_config) as connector:
                connector.get_login_cookie()
                switch_data = connector.get_switch_infos()
            up = True
        except TargetBusyError:
            PROBES_TOTAL.labels(result="busy").inc()
            self._respond(
                503, f"probe already in progress for target {target!r}\n".encode()
            )
            return
        except Exception:
            _LOGGER.exception("probe of target %s failed", target)
            self.server.pool.drop(target)
            switch_data = {}
            up = False

        duration = time.perf_counter() - start
        PROBES_TOTAL.labels(result="success" if up else "error").inc()
        families = build_metric_families(
            switch_data, target=target, up=up, probe_duration_seconds=duration
        )
        self._respond(200, _render_probe_metrics(families), content_type=CONTENT_TYPE_LATEST)

    def _respond(self, status: int, body: bytes, *, content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _ExporterHTTPServer(http.server.HTTPServer):
    """HTTPServer that dispatches connections to a bounded thread pool."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        config: ExporterConfig,
        pool: ConnectorPool,
        *,
        max_concurrent_requests: int,
    ) -> None:
        super().__init__(server_address, _RequestHandler)
        self.config = config
        self.pool = pool
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent_requests, thread_name_prefix="netgear-plus-probe"
        )

    def process_request(self, request, client_address) -> None:  # noqa: ANN001
        self._executor.submit(self._process_request_in_thread, request, client_address)

    def _process_request_in_thread(self, request, client_address) -> None:  # noqa: ANN001
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

    def server_close(self) -> None:
        super().server_close()
        self._executor.shutdown(wait=False)


def create_server(
    listen_address: str,
    listen_port: int,
    config: ExporterConfig,
    *,
    max_concurrent_requests: int = 10,
) -> _ExporterHTTPServer:
    pool = ConnectorPool()
    return _ExporterHTTPServer(
        (listen_address, listen_port),
        config,
        pool,
        max_concurrent_requests=max_concurrent_requests,
    )
