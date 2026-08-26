# netgear-plus-exporter

A Prometheus exporter for NETGEAR "Plus" series smart-managed switches (GS3xx/GS1xx/JGS/MS/XS
models with a local web UI but no SNMP or CLI). It uses
[`py-netgear-plus`](https://github.com/foxey/py-netgear-plus) to log in to each switch's web UI
and scrape port traffic, link status, CRC errors, and PoE stats.

The exporter follows the same **multi-target `/probe` pattern** as `snmp_exporter` and
`blackbox_exporter`, including how configuration is split between the two files:

- `netgear_plus.yml` (the exporter's own config) defines a small number of reusable
  **modules** -- named credential profiles -- the same way snmp_exporter's `snmp.yml` defines
  reusable auth modules. It does *not* list individual switches.
- `prometheus.yml` lists the actual switch inventory (`target`) and, per target group, which
  module's credentials to use (`module`) -- exactly where snmp_exporter puts that information too.

So switches are inventoried in exactly one place (`prometheus.yml`); `netgear_plus.yml` only grows
when you introduce a *new password*, not a new switch.

## Install (development)

Requires **Python 3.10+**. `pip install` already refuses to install this package under an older
interpreter (it's declared via `requires-python` in `pyproject.toml`), and `netgear-plus-exporter`
also checks at startup and exits with a clear error rather than an interpreter crash, in case an
older Python ever ends up running it some other way (e.g. `--ignore-requires-python`, or invoking
the package directly without going through `pip install`).

```console
$ python3 -m venv .venv
$ source .venv/bin/activate
$ pip install -e ".[dev]"
```

(`make venv` does the same thing, and is what `make test` uses to set up its virtualenv --
see "Running tests" below.)

The project always runs inside this virtualenv -- there's no supported system-wide install path.
`pip install -e` builds the `netgear-plus-exporter` console script, from the `[project.scripts]`
entry point in `pyproject.toml`, straight into `.venv/bin/netgear-plus-exporter`. That's the exact
binary the example systemd unit's `ExecStart` points at, so once this step is done you can either
run it directly:

```console
$ .venv/bin/netgear-plus-exporter --config netgear_plus.yml
```

or, with the virtualenv activated, just `netgear-plus-exporter ...` (see below).

## Running tests

```console
$ make test
```

This creates/updates `.venv` if needed (re-run automatically whenever `pyproject.toml` changes),
then runs `pytest` followed by `ruff check .`. Equivalent to running those two manually with the
virtualenv from above activated. `pytest` covers config parsing, the metrics mapping, and the HTTP
server (including the concurrency behavior described in "Slow switches" below) against a fake
connector -- no real switch or network access needed.

Other targets: `make venv` (just create/update the virtualenv), `make lint` (`ruff check .` only),
`make clean` (remove `.venv` and caches).

### Integration tests (real hardware)

`make test-integration` runs a separate, opt-in suite
([`tests/integration/`](tests/integration/test_live_switches.py)) against an already-running
`netgear-plus-exporter` process and real switches -- not fakes. It's excluded from `make test` /
plain `pytest` via the `integration` marker, and always runs serially (no pytest-xdist): concurrent
probes of the *same* target would race the exporter's own single-flight lock.

```console
$ NETGEAR_PLUS_EXPORTER_URL=http://localhost:9493 \
  NETGEAR_PLUS_INTEGRATION_TARGETS="192.168.1.5,192.168.1.6:office" \
  make test-integration
```

Both env vars are optional (defaults: `http://localhost:9493`, and a placeholder target list you
should override). `NETGEAR_PLUS_INTEGRATION_TARGETS` is a comma-separated `host[:module]` list; a
target with no `:module` suffix uses the `default` module. If the exporter isn't reachable at
`NETGEAR_PLUS_EXPORTER_URL`, the whole suite skips cleanly rather than failing.

Each target is probed twice (to check that the receive-bytes counter is non-decreasing) with a few
retries on transient failure, since real Plus switches are slow enough that an occasional failed
probe isn't on its own a bug -- see "Slow switches" below. A target that fails every retry attempt
is a real signal worth investigating (wrong password for its module, the switch being unreachable,
or an unsupported model), not a flaky test.

## Configure

Copy [`netgear_plus.yml.example`](netgear_plus.yml.example) to `netgear_plus.yml` and define one
module per distinct password your switches use -- most setups only need `default`:

```yaml
modules:
  default:
    password: "changeme"
  office:
    password: "changeme2"
    model: GS308EPP   # optional, skips autodetection
```

Run the exporter:

```console
$ netgear-plus-exporter --config netgear_plus.yml --web.listen-port 9493
```

Try a probe directly:

```console
$ curl 'http://localhost:9493/probe?target=192.168.1.5&module=default'
```

`module` may be omitted if the target uses the `default` module.

## Wire it up to Prometheus

```yaml
scrape_configs:
  - job_name: netgear_plus
    scrape_interval: 30s
    scrape_timeout: 30s        # see "Slow switches" below -- raise if needed
    metrics_path: /probe
    static_configs:
      # Switches using the 'default' module's password.
      - targets:
          - "192.168.1.5"
          - "192.168.1.6"
        labels:
          module: default
      # Switches using a different password -- add one block per module.
      - targets:
          - "switch-office.lan"
        labels:
          module: office
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [module]
        target_label: __param_module
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: localhost:9493   # the exporter itself
```

Prometheus scrapes `http://localhost:9493/probe?target=<address>&module=<name>` for each entry in
`static_configs`, exactly as it would `snmp_exporter`. Adding, removing, or relabeling a switch is
a `prometheus.yml`-only change; `netgear_plus.yml` and the exporter process are untouched unless
you're introducing a new password. The exporter's own process/build metrics live on `GET
/metrics`, separate from per-switch data on `/probe`.

## Slow switches

NETGEAR Plus switches have slow embedded web servers -- a single probe issues several sequential
requests (login, metadata, port status, port statistics, and for PoE models, PoE config/status,
then logout), and each of those can individually take multiple seconds to respond. Two things to
tune if you see probe failures or timeouts:

- **`scrape_timeout`** in `prometheus.yml` for this job: the default of 10s is usually too short.
  Start around 30s and increase if you still see timeouts.
- **`--probe-timeout`** (default 20s): the per-HTTP-request timeout the exporter uses when talking
  to a switch. A full probe can issue up to ~6 sequential requests, so worst case probe duration
  is roughly `6 * probe-timeout`. Watch `netgear_plus_probe_duration_seconds` after running for a
  while to tune both values to your actual hardware.

Every probe logs in, collects, and logs back out again, rather than keeping a session open
between scrapes -- these switches allow only one session at a time, and holding one open for the
whole polling interval would lock a human admin out of the web UI.

If a probe for a given target is still in flight when another scrape of the *same* target comes
in (e.g. a retry after a slow response), the exporter fails that second request immediately with
`503` rather than queuing it -- these switches appear to support only one active session at a
time, so queuing would just stack up worker threads behind an already-slow device. Different
targets are always probed concurrently, bounded by `--web.max-concurrent-probes` (default 10).

## Metrics

All switch metrics are prefixed `netgear_plus_` and carry a `target` label (the value from the
scrape's `target` param); per-port metrics also carry a `port` label.

| Metric | Type | Notes |
| --- | --- | --- |
| `netgear_plus_up` | gauge | 1 if the probe succeeded |
| `netgear_plus_probe_duration_seconds` | gauge | wall-clock time of the probe |
| `netgear_plus_switch_info` | gauge | labels: name, serial_number, bootloader, firmware, ip, model |
| `netgear_plus_port_link_up` | gauge | |
| `netgear_plus_port_link_speed_mbps` | gauge | negotiated speed, 0 if down/unknown |
| `netgear_plus_port_info` | gauge | labels: description; only emitted for ports with a description configured on the switch |
| `netgear_plus_port_receive_bytes_total` / `_transmit_bytes_total` | counter | cumulative since switch boot; see precision note below |
| `netgear_plus_port_receive_speed_bytes` / `_transmit_speed_bytes` | gauge | bytes/sec, as computed by py-netgear-plus over its own polling interval |
| `netgear_plus_port_crc_errors_total` | counter | |
| `netgear_plus_port_poe_status` | gauge | PoE-capable models only |
| `netgear_plus_port_poe_power_watts` | gauge | PoE-capable models only |

**Precision note:** py-netgear-plus's public API reports traffic in megabytes (rounded to 0.01
MB), not raw bytes, so the byte counters above carry roughly +/-10KB of quantization noise --
this is a limitation of the upstream library, not of the exporter.

**Alert on `netgear_plus_up`, not the built-in `up`.** Like `blackbox_exporter` and
`snmp_exporter`, this exporter is scraped through `/probe`, so Prometheus's automatic
`up{job="netgear_plus"}` series only reflects whether *this exporter process* answered the scrape
-- it stays `1` even if every switch is unreachable, since the exporter itself is still up.
`netgear_plus_up{target="..."}` is the one that reflects whether the probe of that specific switch
actually succeeded.

## Deploying

An example systemd unit is in [`systemd/netgear-plus-exporter.service`](systemd/netgear-plus-exporter.service).
Adjust the paths for wherever you install the venv and `netgear_plus.yml`, then:

```console
$ sudo cp systemd/netgear-plus-exporter.service /etc/systemd/system/
$ sudo systemctl daemon-reload
$ sudo systemctl enable --now netgear-plus-exporter
```
