from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from mikrotik_bot.whitelist import WhitelistACL, parse_whitelist_entries


class NotifyTarget(BaseModel):
    token_env: str
    # Omit / null → anyone who /start'd this bot can get pushes + commands
    # List entries: numeric Telegram user id and/or @username / username
    whitelist: list[int | str] | None = None

    @field_validator("whitelist")
    @classmethod
    def normalize_whitelist(
        cls, value: list[object] | None
    ) -> list[int | str] | None:
        if value is None:
            return None
        acl = parse_whitelist_entries(list(value))
        # Keep a stable mixed list for debugging / re-serialize
        out: list[int | str] = sorted(acl.user_ids)
        out.extend(f"@{u}" for u in sorted(acl.usernames))
        return out


@dataclass
class ResolvedNotify:
    token: str
    # None = open (all active subscribers); ACL = filter by id/username
    whitelist: WhitelistACL | None


class Thresholds(BaseModel):
    cpu: float = 85
    memory: float = 85
    disk: float = 90


class BuiltinOids(BaseModel):
    uptime: str = "1.3.6.1.2.1.1.3.0"
    cpu: str = "1.3.6.1.2.1.25.3.3.1.2.1"
    mem_size: str = "1.3.6.1.2.1.25.2.3.1.5.65536"
    mem_used: str = "1.3.6.1.2.1.25.2.3.1.6.65536"
    mem_unit: str = "1.3.6.1.2.1.25.2.3.1.4.65536"
    disk_size: str = "1.3.6.1.2.1.25.2.3.1.5.131072"
    disk_used: str = "1.3.6.1.2.1.25.2.3.1.6.131072"
    disk_unit: str = "1.3.6.1.2.1.25.2.3.1.4.131072"


class CustomMetric(BaseModel):
    id: str
    name: str
    oid: str
    type: Literal["gauge", "percent"] = "gauge"
    unit: str = ""
    warn: float | None = None
    critical: float | None = None

    @field_validator("id")
    @classmethod
    def id_slug(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(ch.isspace() for ch in cleaned):
            raise ValueError("metric id must be a non-empty slug without spaces")
        return cleaned


class DeviceConfig(BaseModel):
    id: str
    name: str
    # false → file stays on disk, device is ignored (no SNMP, no env checks)
    enabled: bool = True
    host: str
    port: int = 161
    community_env: str
    interval: int = Field(default=30, ge=5)
    fail_limit: int = Field(default=3, ge=1)
    timezone: str = "UTC"
    notify: list[NotifyTarget] = Field(min_length=1)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    oids: BuiltinOids = Field(default_factory=BuiltinOids)
    metrics: list[CustomMetric] = Field(default_factory=list)
    status_schedule: list[str] = Field(default_factory=list)
    # Optional: MikroTik → bot HTTP hook auth (env var name with shared secret)
    hook_secret_env: str | None = None

    # Resolved at load time from env
    community: str = ""
    hook_secret: str = ""
    notify_resolved: list[ResolvedNotify] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def device_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(ch.isspace() for ch in cleaned):
            raise ValueError("device id must be a non-empty slug without spaces")
        return cleaned

    @field_validator("status_schedule")
    @classmethod
    def validate_times(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values:
            item = raw.strip()
            parts = item.split(":")
            if len(parts) != 2:
                raise ValueError(f"invalid status_schedule time: {raw!r} (want HH:MM)")
            hour, minute = int(parts[0]), int(parts[1])
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError(f"invalid status_schedule time: {raw!r}")
            cleaned.append(f"{hour:02d}:{minute:02d}")
        return cleaned


class AppSettings(BaseModel):
    lang: str = "en"
    config_dir: str = "/app/config/devices"
    state_dir: str = "/data/state"
    locales_dir: str = "/app/locales"
    # 0 = hook listener disabled
    hook_port: int = 8443
    hook_host: str = "0.0.0.0"
