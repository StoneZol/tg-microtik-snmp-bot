# MikroTik SNMP → Telegram bot

Poll one or many MikroTik routers over SNMPv2c and push alerts to Telegram.  
Designed to run in Docker: drop an `.env`, add device YAML files, `docker compose up -d`.

## What it does

- Reachability (SNMP failures → down / recovered)
- CPU, memory, disk thresholds (with hysteresis — no spam)
- Reboot detection via `sysUpTime` going backwards
- Custom OID metrics (warn / critical)
- Per-device poll interval and Telegram bots (optional user whitelist)
- Scheduled full status dumps (`status_schedule`)
- On-demand: `/status`, `/alerts`, `/devices`, `/help` + quick reply keyboard
- HTTP hooks from RouterOS → fan-out to the same `notify` / whitelist audience

## Quick start (Docker)

After clone — create files, fill them however you like (nano on the server, or edit locally and copy up).

```bash
cd tg-microtik-snmp-bot

# secrets
cp .env.example .env
nano .env

# first device (pick any name instead of office)
cp config/devices/office.example.yaml config/devices/office.yaml
nano config/devices/office.yaml
```

Add more routers the same way:

```bash
cp config/devices/office.example.yaml config/devices/site2.yaml
nano config/devices/site2.yaml
```

In YAML, `community_env` / `token_env` are **names of variables** from `.env` (not the secret values themselves).  
Every `config/devices/*.yaml` loads on startup; `*.example.yaml` is ignored. Real device configs are gitignored.

```bash
docker compose up -d --build
docker compose logs -f
```

After edits:

```bash
docker compose restart                      # yaml / locales
docker compose up -d --force-recreate       # if you changed .env
```

Then in Telegram: `/start` (quick keyboard appears) → **Status** / **Alerts** / **Devices**.  
State: `./data/state` (JSON + `subscribers.sqlite`).

---

## Config layout

```
.env                      # secrets only (tokens, communities)
config/devices/
  office.yaml             # one file per router
  dc.yaml
locales/
  en.json                 # Telegram string templates
data/state/               # JSON per device + subscribers.sqlite
```

### Device YAML (essentials)

| Field | Meaning |
|--------|---------|
| `id` | Stable slug (`office`) — used in `/status office` and state filename |
| `enabled` | `true` (default) / `false` — park a checker without deleting the file |
| `host` / `port` | SNMP target |
| `community_env` | Name of env var with community string |
| `interval` | Seconds between polls **for this device** |
| `fail_limit` | Consecutive SNMP errors before “down” alert |
| `timezone` | IANA TZ for `status_schedule` (e.g. `Europe/Moscow`) |
| `notify` | List of bots for this device (see below) |
| `thresholds` | `cpu` / `memory` / `disk` percent limits |
| `oids` | Built-in metric OIDs (override per board) |
| `metrics` | Extra gauges (`id`, `name`, `oid`, `unit`, `warn`, `critical`) |
| `status_schedule` | Extra full snapshots, e.g. `["09:00", "18:00"]` |
| `hook_secret_env` | Optional env var with shared secret for HTTP events from this router |

### `notify` + whitelist

```yaml
notify:
  - token_env: BOT_MAIN_TOKEN
    # no whitelist → every user who /start'd this bot gets pushes + commands
  - token_env: BOT_OWNER_TOKEN
    whitelist:
      - 123456789
      - "@alice"
      - bob
```

| Setup | Who can talk / get pushes |
|--------|---------------------------|
| `whitelist` omitted | Anyone who pressed `/start` on that bot (stored in SQLite) |
| `whitelist: […]` | Only matching user ids / usernames — and only after they `/start` |

Telegram will not deliver DMs until the user has opened the bot at least once. SQLite is required for that “active users” list; alert hysteresis still uses JSON files.

### Translations

Built-in: `locales/en.json`, `locales/ru.json`.  
Set in `.env`:

```bash
BOT_LANG=ru
```

Then recreate the container (`docker compose up -d --force-recreate`) so the env applies.  
Placeholders like `{name}`, `{host}`, `{value}` must stay intact if you edit locale files.

---

## Telegram commands

Private chats only. Access is checked against each device’s whitelist for that bot token.  
`/start` also installs a **quick keyboard** (Status / Alerts / Devices / Help) and registers the bot command menu.

| Command / button | Action |
|---------|--------|
| `/start` / `/help` | Register + help + show keyboard |
| `/status` / **Status** | Short OK / PROBLEMS / DOWN list + `/status_<id>` links |
| `/status <id>` or `/status_<id>` | Full metrics for one device |
| `/alerts` / **Alerts** | Currently active threshold / outage flags |
| `/devices` / **Devices** | List device ids |

---

## Router → bot HTTP hooks (events)

MikroTik does **not** need bot token / chat ids. It posts an event to your VDS; the bot looks up `device_id`, checks `secret`, and fans out via that device’s `notify` list (same ACL as SNMP alerts).

### Port

Default **`HOOK_PORT=8443`** (mapped in `docker-compose.yml`).  
Use a free port if SoftEther/VPN already owns `443`/`80`.

```env
HOOK_PORT=8443
HOOK_SECRET_OFFICE=long-random-string
```

```yaml
# in device yaml
hook_secret_env: HOOK_SECRET_OFFICE
```

`HOOK_PORT=0` disables the listener.

### Contract

**Endpoint:** `POST` or `GET` `/hooks/v1/event`  
**Health:** `GET` `/hooks/health`

**JSON body** (preferred):

```json
{
  "device_id": "office",
  "secret": "long-random-string",
  "event": "login_failure",
  "severity": "critical",
  "message": "login failure for user admin from 1.2.3.4"
}
```

| Field | Required | Notes |
|--------|----------|--------|
| `device_id` | yes | Must match yaml `id` |
| `secret` | yes | Must match env from `hook_secret_env` |
| `event` | no | Default `custom`. Suggested: `login_failure`, `netwatch_down`, `netwatch_up`, `firewall`, `system`, `custom` |
| `severity` | no | `info` \| `warn` \| `critical` (default `info`) |
| `message` | no | Free text / log line |

Aliases accepted: `id`/`device`, `msg`/`text`, `type`/`topic`, `level`.  
Form-urlencoded and query-string fields work the same (handy for simple `/tool fetch`).

**Responses:** `{"ok":true,...}` or `401` unauthorized / `400` bad request.

Example probe from the VDS:

```bash
curl -sS -X POST "http://127.0.0.1:8443/hooks/v1/event" \
  -H "Content-Type: application/json" \
  -d '{"device_id":"office","secret":"long-random-string","event":"login_failure","severity":"critical","message":"test from curl"}'
```

### Minimal RouterOS 7 script (POST JSON)

Replace `HOST`, `SECRET`, and keep `device_id` equal to yaml `id`:

```text
/system script
add name=tg-hook-login policy=read,write,test,sensitive source={
  :local host "http://YOUR_VDS_IP:8443"
  :local secret "long-random-string"
  :local deviceId "office"
  :local msg "login failure (see router log)"
  :local body ("{\"device_id\":\"" . $deviceId . "\",\"secret\":\"" . $secret . "\",\"event\":\"login_failure\",\"severity\":\"critical\",\"message\":\"" . $msg . "\"}")
  /tool fetch url=($host . "/hooks/v1/event") http-method=post http-header-field="content-type: application/json" http-data=$body keep-result=no
}
```

Then wire a **logging action** (or scheduler) that runs this script on login-failure topics.  
Prefer firewall: allow `HOOK_PORT` **only from the router’s WAN IP**.

HTTPS on this port is optional later (Caddy/nginx on `8443` only); plain HTTP behind IP allowlist is OK to start.

---

## Discover OIDs on MikroTik (RouterOS)

Enable SNMP on the router (Winbox / WebFig / CLI), allow your bot host, set community.

### From a Linux box with net-snmp tools

```bash
# System description / uptime
snmpget -v2c -c COMMUNITY ROUTER_IP 1.3.6.1.2.1.1.1.0
snmpget -v2c -c COMMUNITY ROUTER_IP 1.3.6.1.2.1.1.3.0

# Walk host resources (CPU, storage)
snmpwalk -v2c -c COMMUNITY ROUTER_IP 1.3.6.1.2.1.25.3.3.1.2
snmpwalk -v2c -c COMMUNITY ROUTER_IP 1.3.6.1.2.1.25.2.3.1

# MikroTik enterprise tree (temperature, voltage, etc.)
snmpwalk -v2c -c COMMUNITY ROUTER_IP 1.3.6.1.4.1.14988
```

### Useful MikroTik enterprise OIDs (often present)

| Metric | OID (check on your ROS version) |
|--------|----------------------------------|
| Board temperature | `1.3.6.1.4.1.14988.1.1.3.10.0` |
| Board voltage | `1.3.6.1.4.1.14988.1.1.3.8.0` |
| Active firmware | `1.3.6.1.4.1.14988.1.1.4.4.0` |

Storage indexes (`65536` memory, `131072` disk in the example) **differ by model**.  
Always walk `hrStorageTable` and paste the indexes you actually see into `oids:`.

### From RouterOS terminal (if SNMP tools package / script available)

```text
/snmp print
/system package update check-for-updates
```

Prefer walking from an external host — that is what the bot does.

### Minimal RouterOS SNMP enable sketch

```text
/snmp
set enabled=yes contact="noc@example.com" location="office"
/snmp community
set [ find default=yes ] name=YOUR_COMMUNITY read-access=yes
```

Lock SNMP down with firewall / interface lists. Do not expose community to the internet.

---

## Local run (without Docker)

Needs Python 3.12+:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp config/devices/office.example.yaml config/devices/office.yaml
# edit .env + yaml
set PYTHONPATH=src   # Windows PowerShell: $env:PYTHONPATH="src"
python -m mikrotik_bot
```

---

## Architecture (short)

- One async poll loop per device (`interval` from its YAML)
- Shared SNMPv2c client (`pysnmp`) and Telegram HTTP client (`httpx`)
- Long-poll `getUpdates` per unique bot token for commands
- JSON state per device under `STATE_DIR` for hysteresis and reboot detection
- SQLite `subscribers.sqlite` for bot activations + whitelist filtering

Original single-file prototype kept as `original.py` for reference.
