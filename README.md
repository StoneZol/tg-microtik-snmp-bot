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
- On-demand: `/status`, `/status <id>`, `/devices`, `/help`

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

Then in Telegram: `/start` → `/status`.  
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

Copy `locales/en.json` → `locales/ru.json`, translate values, set `BOT_LANG=ru` in `.env`.  
Placeholders like `{name}`, `{host}`, `{value}` must stay intact.

---

## Telegram commands

Private chats only. Access is checked against each device’s whitelist for that bot token.

| Command | Action |
|---------|--------|
| `/start` / `/help` | Register as active + short help |
| `/devices` | Devices you are allowed to see on this bot |
| `/status` | Live snapshot of those devices |
| `/status <id>` | One device |

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
