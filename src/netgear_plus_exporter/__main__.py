"""CLI entrypoint for netgear-plus-exporter."""

from __future__ import annotations

import sys

MIN_PYTHON = (3, 10)


def _python_version_error(version_info, minimum=MIN_PYTHON):
    """Return an error message if version_info is below minimum, else None.

    Deliberately checked and acted on before any other import in this file:
    the rest of the package uses syntax (e.g. `X | Y` union type hints) that
    is only meaningful on Python >= minimum, so importing those modules
    under an older interpreter would surface a much less helpful error than
    this explicit, early check.
    """
    if tuple(version_info[:2]) < minimum:
        found = f"{version_info[0]}.{version_info[1]}.{version_info[2]}"
        return (
            f"netgear-plus-exporter requires Python {minimum[0]}.{minimum[1]}+ "
            f"(found {found})."
        )
    return None


_version_error = _python_version_error(sys.version_info)
if _version_error is not None:
    sys.stderr.write(_version_error + "\n")
    sys.exit(1)

import argparse  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402

from . import __version__  # noqa: E402
from .config import ConfigError, load_config  # noqa: E402
from .server import create_server  # noqa: E402

_LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "netgear_plus.yml"
DEFAULT_LISTEN_ADDRESS = "0.0.0.0"
DEFAULT_LISTEN_PORT = 9493
DEFAULT_MAX_CONCURRENT_REQUESTS = 10
DEFAULT_REQUEST_TIMEOUT = 20.0


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="netgear-plus-exporter")
    parser.add_argument(
        "--config",
        default=os.environ.get("NETGEAR_PLUS_EXPORTER_CONFIG", DEFAULT_CONFIG_PATH),
        help=(
            "Path to the exporter config file mapping targets to credentials "
            "(default: %(default)s, env: NETGEAR_PLUS_EXPORTER_CONFIG)"
        ),
    )
    parser.add_argument(
        "--web.listen-address",
        dest="listen_address",
        default=DEFAULT_LISTEN_ADDRESS,
        help="Address to listen on (default: %(default)s)",
    )
    parser.add_argument(
        "--web.listen-port",
        dest="listen_port",
        type=int,
        default=DEFAULT_LISTEN_PORT,
        help="Port to listen on (default: %(default)s)",
    )
    parser.add_argument(
        "--web.max-concurrent-probes",
        dest="max_concurrent_requests",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_REQUESTS,
        help=(
            "Maximum number of switch probes handled concurrently; extra "
            "requests queue briefly rather than spawning unbounded threads "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--probe-timeout",
        type=float,
        default=DEFAULT_REQUEST_TIMEOUT,
        help=(
            "Per-HTTP-request timeout (seconds) for each request the exporter "
            "makes to a switch. A single probe issues up to ~5 sequential "
            "requests to the switch, so worst-case probe duration is roughly "
            "5x this value. Real Plus switches can take several seconds per "
            "request, so raise this (and Prometheus's scrape_timeout for this "
            "job) if you see probe failures. (default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: %(default)s)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _apply_request_timeout(timeout_seconds: float) -> None:
    """Configure the per-HTTP-request timeout py-netgear-plus uses internally.

    py-netgear-plus doesn't expose this through NetgearSwitchConnector's
    public API, so we set the module-level constant its fetcher reads at
    call time.
    """
    import py_netgear_plus.fetcher as fetcher_module

    fetcher_module.URL_REQUEST_TIMEOUT = timeout_seconds


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    if args.log_level != "DEBUG":
        # py_netgear_plus logs routine per-request detail (including raw
        # response snippets) at INFO, which drowns out the exporter's own
        # logs. Keep it quiet unless the user asks for DEBUG.
        logging.getLogger("py_netgear_plus").setLevel(logging.WARNING)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        _LOGGER.error("%s", exc)
        return 1

    _apply_request_timeout(args.probe_timeout)

    server = create_server(
        args.listen_address,
        args.listen_port,
        config,
        max_concurrent_requests=args.max_concurrent_requests,
    )
    _LOGGER.info(
        "netgear-plus-exporter listening on %s:%s (%d modules configured)",
        args.listen_address,
        args.listen_port,
        len(config.modules),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
