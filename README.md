# Roborock Local Server

A small, self-hosted server that controls a Roborock robot vacuum on your local network. One container on one port gives you:

- a **slim web UI**: status, start/pause/stop/dock, room cleaning, cleaning settings, map, consumables
- a **REST API**, documented with OpenAPI at `/docs`
- an **MCP endpoint** (`/mcp`, streamable HTTP), so AI agents can check on and control the robot

It is built on [python-roborock](https://github.com/Python-roborock/python-roborock). Developed and tested against a **Roborock QV 35A** (`roborock.vacuum.a168`). Other Roborock vacuums that speak the V1 protocol (most S, Q Revo and Qrevo models) should work too.

## Quick start (Debian / any Docker host)

```bash
git clone <this repo> roborock-local-server && cd roborock-local-server
cp .env.example .env          # optional: edit settings, see below
docker compose up -d --build
```

Open `http://<server-ip>:8083`, enter your Roborock account email, then type in the code Roborock emails you. That's it. The robot shows up a few seconds later.

You need Docker Engine with the Compose plugin (`apt install docker.io docker-compose` or Docker's own repository). If your user isn't in the `docker` group, prefix the commands with `sudo`.

## Starting on boot

No extra setup is needed. The container uses `restart: unless-stopped`, so Docker starts it again after a reboot. Only make sure the Docker service itself starts at boot (this is the default on Debian):

```bash
sudo systemctl enable docker
```

| Task | Command |
|---|---|
| Stop | `docker compose stop` (it then stays stopped after reboots until you start it again) |
| Start | `docker compose up -d` |
| Update after `git pull` | `docker compose up -d --build` (your login is kept in the volume) |
| Check | `docker compose ps` and `docker compose logs -f` |

## Configuration

All settings are environment variables. Put them in a `.env` file next to `compose.yaml`; `.env.example` is a template. Nothing personal is stored in the code or image.

| Variable | Default | Description |
|---|---|---|
| `API_TOKEN` | *(empty)* | When set, `/api/*` and `/mcp` require `Authorization: Bearer <token>`. `/api/health` and the UI page stay public, and the UI asks for the token. Generate one with `openssl rand -hex 24`. |
| `ROBOROCK_DEVICE` | *(empty)* | Robot to control: its name from the Roborock app, or its DUID. Empty picks the first supported vacuum. |
| `ROBOROCK_EMAIL` | *(empty)* | Pre-fills the login form and CLI prompt. |
| `PORT` | `8083` | Port for the UI, the API and MCP. |
| `STATUS_CACHE_SECONDS` | `5` | How long one status reading is reused, so several clients polling at once don't flood the robot. |
| `ALLOW_RAW_COMMANDS` | `false` | Turns on `POST /api/command` and the `send_raw_command` MCP tool. |
| `LOG_LEVEL` | `INFO` | `DEBUG` shows protocol details. |
| `DATA_DIR` | `/data` | Where state is stored inside the container. You normally leave this alone. |

After changing `.env`, run `docker compose up -d`.

## How authentication works

Roborock has no purely local pairing. Each robot has a **local key** that only Roborock's cloud hands out. This server therefore does a **one-time login** with the same email-code flow the Roborock app uses:

1. You enter your account email and Roborock emails you a code.
2. You enter the code. The server receives your account data, which includes the robot's local key and cloud tokens.
3. The account data is stored in `/data/credentials.json` (file mode 0600) on the `roborock-data` Docker volume.
4. The device list and network details are cached in `/data/device_cache.pkl`.

After that, commands go **directly to the robot over your LAN** (TCP port 58867). The cloud connection (MQTT) is used only as a fallback, and for a few features the robot only offers through the cloud. The robot stays on the normal Roborock app; nothing is flashed or re-paired.

Things to know:

- Roborock rate-limits code requests. If you request codes repeatedly you may have to wait a while.
- Accounts made through Mi Home / Xiaomi are not supported; use a Roborock app account.
- **Log out** in the UI (or `POST /api/auth/logout`) deletes the stored credentials and cache.
- If Roborock revokes the token, the UI shows a banner and `/api/health` reports it. Log in again.
- Treat the `roborock-data` volume like a password. It contains the token for your Roborock account.

### Logging in from the command line instead

```bash
docker compose run --rm roborock python -m app.cli login
docker compose restart roborock
```

Other CLI commands: `python -m app.cli devices`, `status` and `logout`.

## REST API

Interactive docs are at `http://<server>:8083/docs`. Add `-H "Authorization: Bearer $API_TOKEN"` to the requests below if you set a token.

```bash
curl http://server:8083/api/health                    # public: login/connection state
curl http://server:8083/api/status                    # battery, state, errors, current settings
curl http://server:8083/api/rooms                     # [{segment_id, name}]
curl -X POST http://server:8083/api/start
curl -X POST http://server:8083/api/pause
curl -X POST http://server:8083/api/dock
curl -X POST http://server:8083/api/clean/rooms -H 'Content-Type: application/json' \
     -d '{"rooms": ["Kitchen", "Hallway"], "repeat": 1}'
curl http://server:8083/api/options                   # allowed values for /api/settings
curl -X POST http://server:8083/api/settings -H 'Content-Type: application/json' \
     -d '{"fan_speed": "turbo", "cleaning_mode": "vac_and_mop"}'
curl -o map.png http://server:8083/api/map.png
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Login and connection state (public) |
| GET | `/api/devices` | Devices on the account, and which one is selected |
| GET | `/api/status` | Current status (`?refresh=true` skips the cache) |
| GET | `/api/rooms` | Rooms on the current map |
| GET | `/api/options` | Supported cleaning modes, fan speeds, water levels, mop routes |
| GET | `/api/consumables` | Brush, filter, sensor and dock wear |
| GET | `/api/summary` | Lifetime totals and the last clean |
| GET | `/api/map.png` | Rendered map |
| POST | `/api/start` `/api/pause` `/api/stop` `/api/dock` `/api/locate` | Basic control |
| POST | `/api/clean/rooms` | `{"rooms": [names or IDs], "repeat": 1-3}` |
| POST | `/api/settings` | `{"cleaning_mode", "fan_speed", "water_level", "mop_route"}`, all optional |
| POST | `/api/dock/empty` `/api/dock/wash` | Empty the dustbin / wash the mop (robot must be docked) |
| POST | `/api/consumables/{key}/reset` | Reset a wear counter |
| POST | `/api/auth/request-code` `/api/auth/login` `/api/auth/logout` | Account login |
| POST | `/api/command` | Raw command `{"method", "params"}` (needs `ALLOW_RAW_COMMANDS=true`) |

Errors come back as `{"detail": "..."}`:

| Status | Meaning |
|---|---|
| 400 | Bad input |
| 401 | Missing or wrong token |
| 502 | The robot rejected the command |
| 503 | Not logged in, or the robot is unreachable |

## MCP (for AI agents)

The MCP endpoint is `http://<server>:8083/mcp`. It uses the streamable HTTP transport in stateless mode, so clients keep working across server restarts.

| Tool | What it does |
|---|---|
| `get_status` | Current state, battery, errors, active settings |
| `list_rooms` | Rooms on the current map |
| `start_cleaning` / `pause_cleaning` / `stop_cleaning` / `return_to_dock` / `locate_robot` | Basic control |
| `clean_rooms(rooms, repeat)` | Clean rooms, given by name or ID |
| `get_cleaning_options` / `set_cleaning_mode(...)` | Read and change cleaning mode, fan speed, water level, mop route |
| `get_consumables` / `reset_consumable` | Wear counters |
| `get_cleaning_summary` | Totals and the last clean |
| `empty_dustbin` / `wash_mop` | Dock actions |
| `get_map` | The map as a PNG image |
| `send_raw_command` | Only when `ALLOW_RAW_COMMANDS=true` |

**Claude Code:**

```bash
claude mcp add --transport http roborock http://<server>:8083/mcp \
  --header "Authorization: Bearer <API_TOKEN>"     # omit --header if no token is set
```

**Generic client config** (Claude Desktop via a remote MCP bridge, Cursor, etc.):

```json
{
  "mcpServers": {
    "roborock": {
      "type": "http",
      "url": "http://<server>:8083/mcp",
      "headers": { "Authorization": "Bearer <API_TOKEN>" }
    }
  }
}
```

To try it interactively, run `npx @modelcontextprotocol/inspector` and connect to the URL above.

## Troubleshooting

- **The UI says "cloud" instead of "local".** The server couldn't open a direct connection to the robot. Check that the server can reach the robot's IP on TCP 58867 (`nc -vz <robot-ip> 58867`). Give the robot a fixed IP (a DHCP reservation in your router) so the cached address stays valid. If Docker's bridge network is the problem, set `network_mode: host` in `compose.yaml` and remove the `ports:` section.
- **"Robot is not connected yet".** The robot is asleep, offline or still connecting. The server keeps retrying in the background.
- **A setting value isn't accepted.** Allowed values depend on the model. Ask `GET /api/options`.
- **Logs:** `docker compose logs -f roborock`. Set `LOG_LEVEL=DEBUG` for protocol detail.
- **Start over:** log out in the UI, or `docker compose down -v` (this removes the stored login).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
DATA_DIR=./data .venv/bin/python -m app.main
```

Layout:

| Path | Contents |
|---|---|
| `app/robot.py` | All robot logic (wraps python-roborock) |
| `app/api.py` | REST routes |
| `app/mcp_server.py` | MCP tools |
| `app/auth.py` | Login flow and credential storage |
| `app/security.py` | Token check |
| `app/static/` | Web UI (plain HTML/JS/CSS, no build step) |

## License

Apache-2.0
