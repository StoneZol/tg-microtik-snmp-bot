from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from mikrotik_bot.formatting import (
    build_status_message,
    build_status_summary_block,
    collect_sample_issues,
    format_metric_value,
    format_pct,
    format_usage,
)
from mikrotik_bot.i18n import Translator
from mikrotik_bot.models import DeviceConfig
from mikrotik_bot.snmp_client import Sample, SnmpClient
from mikrotik_bot.state import StateStore
from mikrotik_bot.subscribers import SubscriberStore
from mikrotik_bot.telegram import TelegramClient

log = logging.getLogger(__name__)


class DeviceMonitor:
    def __init__(
        self,
        device: DeviceConfig,
        snmp: SnmpClient,
        telegram: TelegramClient,
        state_store: StateStore,
        subscribers: SubscriberStore,
        translator: Translator,
    ) -> None:
        self.device = device
        self._snmp = snmp
        self._telegram = telegram
        self._state_store = state_store
        self._subscribers = subscribers
        self._tr = translator
        self._tz = ZoneInfo(device.timezone)
        self.state: dict[str, Any] = state_store.load(device.id)
        self._failures = int(self.state.get("failures", 0))
        self._outage_notified = bool(self.state.get("outage_notified", False))

    async def notify(self, text: str) -> None:
        for target in self.device.notify_resolved:
            chat_ids = self._subscribers.recipients(target.token, target.whitelist)
            if not chat_ids:
                log.warning(
                    "[%s] no active subscribers for token …%s (whitelist=%s)",
                    self.device.id,
                    target.token[-6:],
                    "open"
                    if target.whitelist is None
                    else target.whitelist.summary(),
                )
                continue
            await self._telegram.notify_chats(target.token, chat_ids, text)

    async def run_forever(self) -> None:
        extras = (
            self._tr.t("startup_extra_metrics") if self.device.metrics else ""
        )
        try:
            await self.notify(
                self._tr.t(
                    "startup",
                    name=self.device.name,
                    host=self.device.host,
                    extras=extras,
                )
            )
        except Exception:  # noqa: BLE001
            log.exception("[%s] startup notify failed", self.device.id)

        while True:
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                log.exception("[%s] unexpected tick error", self.device.id)
            await asyncio.sleep(self.device.interval)

    async def fetch_sample(self) -> Sample:
        return await self._snmp.sample_device(self.device)

    async def status_text(self) -> str:
        try:
            sample = await self.fetch_sample()
            return build_status_message(self._tr, self.device, sample)
        except Exception as exc:  # noqa: BLE001
            return self._tr.t(
                "status_unreachable",
                name=self.device.name,
                host=self.device.host,
                error=str(exc),
            )

    async def status_summary_text(self) -> str:
        try:
            sample = await self.fetch_sample()
            issues = collect_sample_issues(self._tr, self.device, sample)
            return build_status_summary_block(
                self._tr,
                self.device,
                ok=not issues,
                issues=issues,
            )
        except Exception:  # noqa: BLE001
            return build_status_summary_block(
                self._tr,
                self.device,
                ok=False,
                down=True,
            )

    async def _tick(self) -> None:
        try:
            sample = await self.fetch_sample()
        except Exception as exc:  # noqa: BLE001
            await self._on_failure(exc)
            return

        await self._on_success(sample)
        await self._maybe_scheduled_status(sample)

    async def _on_failure(self, error: Exception) -> None:
        self._failures += 1
        log.warning(
            "[%s] SNMP check failed (%s): %s",
            self.device.id,
            self._failures,
            error,
        )
        self.state["failures"] = self._failures
        self.state["outage_notified"] = self._outage_notified

        if self._failures >= self.device.fail_limit and not self._outage_notified:
            try:
                await self.notify(
                    self._tr.t(
                        "outage",
                        name=self.device.name,
                        host=self.device.host,
                        failures=self._failures,
                    )
                )
                self._outage_notified = True
                self.state["outage_notified"] = True
            except Exception:  # noqa: BLE001
                log.exception("[%s] outage notify failed", self.device.id)

        self._persist()

    async def _on_success(self, sample: Sample) -> None:
        if self._failures >= self.device.fail_limit and self._outage_notified:
            await self.notify(
                self._tr.t(
                    "recovered",
                    name=self.device.name,
                    host=self.device.host,
                )
            )

        self._failures = 0
        self._outage_notified = False
        self.state["failures"] = 0
        self.state["outage_notified"] = False

        previous_uptime = self.state.get("uptime")
        if previous_uptime is not None and sample.uptime_ticks < int(previous_uptime):
            await self.notify(
                self._tr.t(
                    "reboot",
                    name=self.device.name,
                    host=self.device.host,
                )
            )
        self.state["uptime"] = sample.uptime_ticks

        await self._check_threshold(
            key="cpu_alert",
            label=self._tr.t("label_cpu"),
            value=sample.cpu,
            limit=self.device.thresholds.cpu,
            rendered=format_pct(sample.cpu),
            limit_rendered=format_pct(self.device.thresholds.cpu),
        )
        await self._check_threshold(
            key="memory_alert",
            label=self._tr.t("label_memory"),
            value=sample.memory_pct,
            limit=self.device.thresholds.memory,
            rendered=format_usage(
                sample.memory_pct,
                sample.memory_used_bytes,
                sample.memory_total_bytes,
            ),
            limit_rendered=format_pct(self.device.thresholds.memory),
        )
        await self._check_threshold(
            key="disk_alert",
            label=self._tr.t("label_disk"),
            value=sample.disk_pct,
            limit=self.device.thresholds.disk,
            rendered=format_usage(
                sample.disk_pct,
                sample.disk_used_bytes,
                sample.disk_total_bytes,
            ),
            limit_rendered=format_pct(self.device.thresholds.disk),
        )

        for metric in self.device.metrics:
            value = sample.custom.get(metric.id)
            if value is None:
                continue
            rendered = format_metric_value(metric, value)

            if metric.critical is not None:
                await self._check_threshold(
                    key=f"metric:{metric.id}:critical",
                    label=f"{metric.name} (critical)",
                    value=value,
                    limit=metric.critical,
                    rendered=rendered,
                    limit_rendered=format_metric_value(metric, metric.critical),
                )
            if metric.warn is not None:
                # Warn only when not already in critical
                in_critical = bool(
                    self.state.get(f"metric:{metric.id}:critical", False)
                )
                if not in_critical:
                    await self._check_threshold(
                        key=f"metric:{metric.id}:warn",
                        label=f"{metric.name} (warn)",
                        value=value,
                        limit=metric.warn,
                        rendered=rendered,
                        limit_rendered=format_metric_value(metric, metric.warn),
                    )
                elif self.state.get(f"metric:{metric.id}:warn"):
                    self.state[f"metric:{metric.id}:warn"] = False

        log.info(
            "[%s] CPU=%s RAM=%s Disk=%s Uptime=%ss custom=%s",
            self.device.id,
            format_pct(sample.cpu),
            format_pct(sample.memory_pct),
            format_pct(sample.disk_pct),
            sample.uptime_ticks / 100,
            sample.custom,
        )
        self._persist()

    async def _check_threshold(
        self,
        *,
        key: str,
        label: str,
        value: float,
        limit: float,
        rendered: str,
        limit_rendered: str,
    ) -> None:
        active = bool(self.state.get(key, False))
        if value >= limit and not active:
            await self.notify(
                self._tr.t(
                    "threshold_breach",
                    name=self.device.name,
                    host=self.device.host,
                    label=label,
                    value=rendered,
                    limit=limit_rendered,
                )
            )
            self.state[key] = True
        elif value < limit and active:
            await self.notify(
                self._tr.t(
                    "threshold_ok",
                    name=self.device.name,
                    host=self.device.host,
                    label=label,
                    value=rendered,
                )
            )
            self.state[key] = False

    async def _maybe_scheduled_status(self, sample: Sample) -> None:
        if not self.device.status_schedule:
            return

        now = datetime.now(self._tz)
        stamp = now.strftime("%H:%M")
        day = now.strftime("%Y-%m-%d")
        sent: dict[str, str] = dict(self.state.get("status_schedule_sent") or {})

        if stamp not in self.device.status_schedule:
            return
        if sent.get(stamp) == day:
            return

        await self.notify(build_status_message(self._tr, self.device, sample))
        sent[stamp] = day
        # Drop stale keys
        sent = {k: v for k, v in sent.items() if k in self.device.status_schedule}
        self.state["status_schedule_sent"] = sent
        self._persist()

    def _persist(self) -> None:
        self._state_store.save(self.device.id, self.state)
