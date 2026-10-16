from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from mikrotik_bot.models import AppSettings, DeviceConfig, ResolvedNotify
from mikrotik_bot.paths import resolve_dir
from mikrotik_bot.whitelist import WhitelistACL, parse_whitelist_entries

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    pass


def _message_lang() -> str:
    bot_lang = (os.getenv("BOT_LANG") or "").strip()
    if bot_lang:
        return bot_lang

    # Fallback for old configs; ignore OS locales like en_US.UTF-8 / C.UTF-8
    lang = (os.getenv("LANG") or "en").strip() or "en"
    if "_" in lang or "." in lang or lang.upper() in {"C", "POSIX"}:
        return "en"
    return lang


def load_settings() -> AppSettings:
    load_dotenv()
    return AppSettings(
        lang=_message_lang(),
        config_dir=resolve_dir("CONFIG_DIR", "/app/config/devices", "config/devices"),
        state_dir=resolve_dir(
            "STATE_DIR", "/data/state", "data/state", create=True
        ),
        locales_dir=resolve_dir("LOCALES_DIR", "/app/locales", "locales"),
    )


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing or empty environment variable: {name}")
    return value


def load_devices(config_dir: str | Path) -> list[DeviceConfig]:
    directory = Path(config_dir)
    if not directory.is_dir():
        raise ConfigError(f"Config directory not found: {directory}")

    paths = sorted(
        p
        for p in directory.iterdir()
        if p.suffix.lower() in {".yaml", ".yml"} and not p.name.endswith(".example.yaml")
    )

    if not paths:
        raise ConfigError(
            f"No device configs in {directory}. "
            "Copy *.example.yaml to *.yaml and fill real values."
        )

    devices: list[DeviceConfig] = []
    seen_ids: set[str] = set()

    for path in paths:
        with path.open(encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)

        if not isinstance(raw, dict):
            raise ConfigError(f"{path.name}: root must be a mapping")

        device = DeviceConfig.model_validate(raw)
        if not device.enabled:
            log.info(
                "Skipping disabled device %s (%s)",
                device.id,
                path.name,
            )
            continue

        if device.id in seen_ids:
            raise ConfigError(f"Duplicate device id: {device.id}")
        seen_ids.add(device.id)

        device.community = _require_env(device.community_env)
        resolved: list[ResolvedNotify] = []
        for target in device.notify:
            token = _require_env(target.token_env)
            whitelist = (
                None
                if target.whitelist is None
                else parse_whitelist_entries(list(target.whitelist))
            )
            resolved.append(ResolvedNotify(token=token, whitelist=whitelist))
        device.notify_resolved = resolved

        metric_ids = [m.id for m in device.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ConfigError(f"{device.id}: duplicate metric ids")

        devices.append(device)

    if not devices:
        raise ConfigError(
            f"No enabled devices in {directory}. "
            "Set enabled: true on at least one config."
        )

    return devices


def unique_bot_tokens(devices: list[DeviceConfig]) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for device in devices:
        for target in device.notify_resolved:
            if target.token not in seen:
                seen.add(target.token)
                tokens.append(target.token)
    return tokens


def devices_for_token(devices: list[DeviceConfig], token: str) -> list[DeviceConfig]:
    return [
        device
        for device in devices
        if any(t.token == token for t in device.notify_resolved)
    ]


def user_allowed_on_target(
    whitelist: WhitelistACL | None,
    user_id: int | None,
    username: str | None = None,
) -> bool:
    if whitelist is None:
        return True
    return whitelist.allows(user_id, username)


def devices_for_user(
    devices: list[DeviceConfig],
    token: str,
    user_id: int | None,
    username: str | None = None,
) -> list[DeviceConfig]:
    """Devices on this bot that the user may interact with / receive."""
    allowed: list[DeviceConfig] = []
    for device in devices_for_token(devices, token):
        for target in device.notify_resolved:
            if target.token != token:
                continue
            if user_allowed_on_target(target.whitelist, user_id, username):
                allowed.append(device)
                break
    return allowed


def user_may_use_bot(
    devices: list[DeviceConfig],
    token: str,
    user_id: int | None,
    username: str | None = None,
) -> bool:
    return bool(devices_for_user(devices, token, user_id, username))
