from __future__ import annotations

import asyncio
import json
import logging
import secrets
from typing import TYPE_CHECKING, Any

from aiohttp import web

from mikrotik_bot.i18n import Translator
from mikrotik_bot.models import DeviceConfig

if TYPE_CHECKING:
    from mikrotik_bot.monitor import DeviceMonitor

log = logging.getLogger(__name__)

# Documented event names (free-form string is still accepted)
KNOWN_EVENTS = frozenset(
    {
        "login_failure",
        "login_success",
        "netwatch_down",
        "netwatch_up",
        "firewall",
        "system",
        "custom",
    }
)
SEVERITIES = frozenset({"info", "warn", "critical"})


def _pick(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


async def _parse_payload(request: web.Request) -> dict[str, str]:
    """Merge query + JSON body + form into one flat string map."""
    data: dict[str, Any] = dict(request.rel_url.query)
    if request.can_read_body:
        ctype = (request.content_type or "").lower()
        try:
            if "json" in ctype:
                body = await request.json()
                if isinstance(body, dict):
                    data.update(body)
            elif "form" in ctype or ctype in {
                "application/x-www-form-urlencoded",
                "multipart/form-data",
            }:
                form = await request.post()
                data.update(dict(form))
            else:
                # RouterOS sometimes posts JSON without proper content-type
                raw = await request.text()
                raw = raw.strip()
                if raw.startswith("{"):
                    body = json.loads(raw)
                    if isinstance(body, dict):
                        data.update(body)
        except Exception:  # noqa: BLE001
            log.exception("Failed to parse hook body")
            raise web.HTTPBadRequest(text='{"ok":false,"error":"bad_body"}')

    return {
        "device_id": _pick(data, "device_id", "id", "device"),
        "secret": _pick(data, "secret", "token", "hook_secret"),
        "event": _pick(data, "event", "type", "topic") or "custom",
        "message": _pick(data, "message", "msg", "text"),
        "severity": (_pick(data, "severity", "level") or "info").lower(),
    }


def _escape_markdown(text: str) -> str:
    """Escape Telegram legacy Markdown specials (keep intentional ` in message)."""
    for ch in ("\\", "*", "_", "[", "]"):
        text = text.replace(ch, f"\\{ch}")
    return text


def format_hook_message(
    tr: Translator,
    device: DeviceConfig,
    *,
    event: str,
    message: str,
    severity: str,
) -> str:
    sev = severity if severity in SEVERITIES else "info"
    return tr.t(
        "hook_event",
        name=_escape_markdown(device.name),
        host=_escape_markdown(device.host),
        id=_escape_markdown(device.id),
        event=_escape_markdown(event),
        severity=_escape_markdown(tr.t(f"hook_severity_{sev}")),
        message=message or tr.t("hook_no_message"),
    )


def create_hook_app(
    *,
    devices: dict[str, DeviceConfig],
    monitors: dict[str, DeviceMonitor],
    translator: Translator,
) -> web.Application:
    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "mikrotik-hook"})

    async def event(request: web.Request) -> web.Response:
        payload = await _parse_payload(request)
        device_id = payload["device_id"]
        secret = payload["secret"]
        event_name = payload["event"]
        message = payload["message"]
        severity = payload["severity"]

        if not device_id or not secret:
            raise web.HTTPBadRequest(
                text='{"ok":false,"error":"device_id_and_secret_required"}'
            )

        device = devices.get(device_id)
        if device is None or not device.hook_secret:
            # Same response as bad secret — do not leak which ids exist
            raise web.HTTPUnauthorized(text='{"ok":false,"error":"unauthorized"}')

        if not secrets.compare_digest(secret, device.hook_secret):
            raise web.HTTPUnauthorized(text='{"ok":false,"error":"unauthorized"}')

        if severity not in SEVERITIES:
            severity = "info"

        text = format_hook_message(
            translator,
            device,
            event=event_name,
            message=message,
            severity=severity,
        )
        monitor = monitors[device_id]
        try:
            # Markdown so `value` from RouterOS scripts is tap-to-copy in Telegram
            await monitor.notify(text, parse_mode="Markdown")
        except Exception:  # noqa: BLE001
            log.exception("[%s] hook notify failed", device_id)
            raise web.HTTPBadGateway(text='{"ok":false,"error":"notify_failed"}')

        log.info(
            "[%s] hook event=%s severity=%s",
            device_id,
            event_name,
            severity,
        )
        return web.json_response(
            {
                "ok": True,
                "device_id": device_id,
                "event": event_name,
                "severity": severity,
            }
        )

    app.router.add_get("/hooks/health", health)
    app.router.add_get("/hooks/v1/event", event)
    app.router.add_post("/hooks/v1/event", event)
    return app


async def run_hook_server(
    *,
    host: str,
    port: int,
    devices: dict[str, DeviceConfig],
    monitors: dict[str, DeviceMonitor],
    translator: Translator,
) -> None:
    app = create_hook_app(
        devices=devices, monitors=monitors, translator=translator
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    log.info("Hook listener on http://%s:%s/hooks/v1/event", host, port)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
