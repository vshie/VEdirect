# Victron VE.Direct Monitor — BlueOS Extension

A BlueOS extension that reads a Victron solar charge controller (MPPT) over the
**VE.Direct** serial protocol, logs every frame to a timestamped CSV, and
publishes key power metrics to the autopilot via **mavlink2rest** as
`NAMED_VALUE_FLOAT` messages (so they land in the ArduPilot `.BIN` log).

A web interface shows live cards for the controller data plus a configurable,
dual-y-axis chart (defaulting to **load current** and **solar power**).

## Features

- **VE.Direct text-protocol parser** with checksum validation, tolerant of the
  HEX protocol frames some devices interleave.
- **CSV logging** of all decoded fields, with any unrecognised labels preserved
  verbatim in an `extra_json` column so nothing is ever dropped. Logs **rotate
  weekly** — a new file `vedirect_<ISO-year>-W<week>.csv` (e.g.
  `vedirect_2026-W27.csv`) is started each ISO week under `/data`.
- **Embedded file browser** in the GUI (the BlueOS File Browser on port 7777,
  scoped to this extension's data folder) to view and download any weekly log.
- **MAVLink telemetry** to the autopilot. Each metric is sent on its own
  `component_id` (base + offset) so mavlink-server does not overwrite them:

  | Name        | Meaning                     | Units | Component offset |
  |-------------|-----------------------------|-------|------------------|
  | `VE_PV_V`   | PV (solar) array voltage    | V     | base + 0         |
  | `VE_PV_W`   | PV (solar) array power      | W     | base + 1         |
  | `VE_LOAD_A` | Load output current         | A     | base + 2         |
  | `VE_BAT_V`  | Battery voltage             | V     | base + 3         |
  | `VE_BAT_A`  | Battery current (+ = charge)| A     | base + 4         |
  | `VE_OK`     | Liveness heartbeat (`1.0`)  | —     | base + 5         |

  Component ID base defaults to **70** to avoid ranges used by other BlueOS
  extensions.
- **Web GUI**: live metric cards (decoded charge-state / tracker / error text),
  and a configurable dual-axis line chart with selectable metrics and time
  window. Chart selections persist across clients.

## Hardware / wiring

Connect the charge controller's VE.Direct port to the onboard computer with a
Victron **VE.Direct-to-USB** cable. It enumerates as a USB serial device
(e.g. `/dev/ttyUSB0`) at **19200 baud, 8N1**. Set the port in the Settings tab
if it differs.

## Data pushed to the autopilot

The extension POSTs `NAMED_VALUE_FLOAT` messages to
`http://host.docker.internal:6040/v1/mavlink` (the mavlink2rest REST endpoint)
once per poll interval. To confirm they arrive, open the MAVLink Inspector in
BlueOS and look for the `VE_*` names.

## Development

Local run (no device required — it will report "disconnected"):

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=./data PYTHONPATH=. uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Or with Docker Compose (mounts `/dev` so the serial port is visible):

```bash
docker compose up --build
```

Run the parser self-test:

```bash
PYTHONPATH=. python scripts/test_parser.py
```

## Building / releasing

Pushing to `main` (or a git tag) triggers `.github/workflows/build.yml`, which
uses the [`BlueOS-community/Deploy-BlueOS-Extension`](https://github.com/BlueOS-community/Deploy-BlueOS-Extension)
action to build multi-arch images and push them to Docker Hub. Configure these
repository secrets/variables:

- `DOCKER_USERNAME`, `DOCKER_PASSWORD` (Docker Hub credentials)

The image is published as `<docker-username>/blueos-vedirect`.

## Manual install on BlueOS

Open BlueOS → **Extensions** → **Installed** tab → **+** (bottom right) and fill
in the form exactly as below.

| Field                   | Value                                  |
|-------------------------|----------------------------------------|
| **Extension Identifier**| `vshie.vedirect`                       |
| **Extension Name**      | `Victron VE.Direct Monitor`            |
| **Docker image**        | `vshie/blueos-vedirect`                |
| **Docker tag**          | `main`                                 |

> The Docker image is `<DOCKER_USERNAME>/blueos-vedirect`, where `DOCKER_USERNAME`
> is the Docker Hub account configured in the build workflow. For this repo that
> is `vshie`, giving `vshie/blueos-vedirect`. Use a released version tag (e.g.
> `1.0.0`) instead of `main` once you tag a release.

**Custom settings** — paste this JSON verbatim (privileged access + `/dev` bind
for the serial port, and a `/data` bind for the persistent CSV log and
`settings.json`):

```json
{
  "ExposedPorts": {
    "80/tcp": {}
  },
  "HostConfig": {
    "Privileged": true,
    "ExtraHosts": ["host.docker.internal:host-gateway"],
    "PortBindings": {
      "80/tcp": [
        {
          "HostPort": ""
        }
      ]
    },
    "Binds": [
      "/usr/blueos/extensions/vedirect:/data",
      "/dev:/dev"
    ]
  }
}
```

After it installs and starts, the extension appears in the BlueOS sidebar as
**Victron VE.Direct Monitor**. If your VE.Direct cable enumerates as something
other than `/dev/ttyUSB0`, change it in the extension's **Settings** tab.

## API

| Endpoint             | Method | Description                                  |
|----------------------|--------|----------------------------------------------|
| `/register_service`  | GET    | BlueOS service registration                  |
| `/api/status`        | GET    | Connection state + latest decoded values     |
| `/api/metrics`       | GET    | Numeric columns available for charting        |
| `/api/settings`      | GET/PUT| Read / update settings                       |
| `/api/history`       | GET    | Recent CSV rows (`?minutes=`), across weekly files |
| `/api/download/csv`  | GET    | Download the current (newest) weekly CSV     |

## License

AGPLv3, consistent with the BlueOS extension ecosystem.
