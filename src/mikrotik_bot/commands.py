from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from mikrotik_bot.config import devices_for_user, user_may_use_bot
from mikrotik_bot.i18n import Translator
from mikrotik_bot.models import DeviceConfig
from mikrotik_bot.subscribers import SubscriberStore
from mikrotik_bot.telegram import TelegramClient

if TYPE_CHECKING:
    from mikrotik_bot.monitor import DeviceMonitor

log = logging.getLogger(__name__)

_BOT_COMMAND_KEYS = (
    ("start", "bot_cmd_start"),
    ("status", "bot_cmd_status"),
    ("devices", "bot_cmd_devices"),
    ("alerts", "bot_cmd_alerts"),
    ("help", "bot_cmd_help"),
)


def localized_bot_commands(tr: Translator) -> list[dict[str, str]]:
    return [
        {"command": cmd, "description": tr.t(key)}
        for cmd, key in _BOT_COMMAND_KEYS
    ]


def main_keyboard(tr: Translator) -> dict[str, Any]:
    return {
        "keyboard": [
            [
                {"text": tr.t("btn_status")},
                {"text": tr.t("btn_alerts")},
            ],
            [
                {"text": tr.t("btn_devices")},
                {"text": tr.t("btn_help")},
            ],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


class CommandListener:
    """Long-poll Telegram getUpdates for each unique bot token."""

    def __init__(
        self,
        telegram: TelegramClient,
        translator: Translator,
        devices: list[DeviceConfig],
        monitors: dict[str, DeviceMonitor],
        subscribers: SubscriberStore,
        tokens: list[str],
    ) -> None:
        self._telegram = telegram
        self._tr = translator
        self._devices = devices
        self._monitors = monitors
        self._subscribers = subscribers
        self._tokens = tokens
        self._offsets: dict[str, int] = {}
        self._button_map = {
            translator.t("btn_status"): "/status",
            translator.t("btn_devices"): "/devices",
            translator.t("btn_alerts"): "/alerts",
            translator.t("btn_help"): "/help",
        }

    async def run_forever(self) -> None:
        commands = localized_bot_commands(self._tr)
        for token in self._tokens:
            try:
                await self._telegram.set_my_commands(token, commands)
            except Exception:  # noqa: BLE001
                log.exception("Failed to register bot commands menu")
        await asyncio.gather(*(self._poll_token(token) for token in self._tokens))

    async def _poll_token(self, token: str) -> None:
        while True:
            try:
                offset = self._offsets.get(token)
                updates = await self._telegram.get_updates(
                    token, offset=offset, timeout=25
                )
                for update in updates:
                    update_id = int(update["update_id"])
                    self._offsets[token] = update_id + 1
                    message = update.get("message") or update.get("edited_message")
                    if not message:
                        continue
                    await self._handle_message(token, message)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Telegram poll error for a bot token")
                await asyncio.sleep(5)

    def _normalize_text(self, text: str) -> str:
        mapped = self._button_map.get(text)
        return mapped if mapped else text

    async def _handle_message(self, token: str, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        chat_type = chat.get("type") or "private"
        if not chat_id or chat_type != "private":
            return

        from_user = message.get("from") or {}
        user_id = from_user.get("id")
        user_id_int = int(user_id) if user_id is not None else None
        username = from_user.get("username")

        raw = (message.get("text") or "").strip()
        if not raw:
            return

        text = self._normalize_text(raw)
        is_command = text.startswith("/")
        is_button = raw in self._button_map
        if not is_command and not is_button:
            return

        if not user_may_use_bot(self._devices, token, user_id_int, username):
            log.info(
                "Ignoring command from unauthorized user %s (@%s) chat %s",
                user_id_int,
                username or "?",
                chat_id,
            )
            try:
                await self._telegram.send_message(
                    token, chat_id, self._tr.t("cmd_denied")
                )
            except Exception:  # noqa: BLE001
                log.exception("Failed to send denial message")
            return

        self._subscribers.touch(
            token,
            chat_id,
            user_id=user_id_int,
            username=username,
        )

        head, _, rest = text.partition(" ")
        command = head.split("@", 1)[0].lower()
        arg = rest.strip()

        # /status_<device_id> — tap-friendly one-device status from /devices list
        if command.startswith("/status_") and command != "/status_":
            arg = command[len("/status_") :]
            command = "/status"

        scoped = devices_for_user(
            self._devices, token, user_id_int, username
        )
        keyboard = main_keyboard(self._tr)

        if command in {"/start", "/help"}:
            await self._telegram.send_message(
                token,
                chat_id,
                self._tr.t("cmd_help"),
                reply_markup=keyboard,
            )
            return

        if command == "/devices":
            if not scoped:
                await self._telegram.send_message(
                    token,
                    chat_id,
                    self._tr.t("cmd_no_devices"),
                    reply_markup=keyboard,
                )
                return
            blocks = [
                self._tr.t(
                    "cmd_devices_line",
                    id=device.id,
                    name=device.name,
                    host=device.host,
                )
                for device in scoped
            ]
            text = self._tr.t("cmd_devices_header") + "\n\n" + "\n\n".join(blocks)
            await self._telegram.send_message(
                token, chat_id, text, reply_markup=keyboard
            )
            return

        if command == "/alerts":
            await self._telegram.send_message(
                token,
                chat_id,
                self._alerts_text(scoped),
                reply_markup=keyboard,
            )
            return

        if command == "/status":
            if not scoped:
                await self._telegram.send_message(
                    token,
                    chat_id,
                    self._tr.t("cmd_no_devices"),
                    reply_markup=keyboard,
                )
                return

            if arg:
                device = next((d for d in scoped if d.id == arg), None)
                if device is None:
                    await self._telegram.send_message(
                        token,
                        chat_id,
                        self._tr.t("cmd_unknown_device", id=arg),
                        reply_markup=keyboard,
                    )
                    return
                detail = await self._monitors[device.id].status_text()
                await self._telegram.send_message(
                    token,
                    chat_id,
                    detail,
                    reply_markup=keyboard,
                )
                return

            # Global status: short OK / problems list + /status_<id> links
            blocks = [
                await self._monitors[device.id].status_summary_text()
                for device in scoped
            ]
            text = (
                self._tr.t("cmd_status_summary_header")
                + "\n\n"
                + "\n\n".join(blocks)
            )
            await self._telegram.send_message(
                token,
                chat_id,
                text,
                reply_markup=keyboard,
            )
            return

    def _alerts_text(self, scoped: list[DeviceConfig]) -> str:
        if not scoped:
            return self._tr.t("cmd_no_devices")

        blocks: list[str] = [self._tr.t("cmd_alerts_header")]
        any_active = False
        for device in scoped:
            monitor = self._monitors[device.id]
            labels: list[str] = []
            if monitor.state.get("outage_notified") or getattr(
                monitor, "_outage_notified", False
            ):
                labels.append(self._tr.t("alert_snmp_down"))
            if monitor.state.get("cpu_alert"):
                labels.append(self._tr.t("label_cpu"))
            if monitor.state.get("memory_alert"):
                labels.append(self._tr.t("label_memory"))
            if monitor.state.get("disk_alert"):
                labels.append(self._tr.t("label_disk"))

            by_id = {m.id: m for m in device.metrics}
            for key, active in monitor.state.items():
                if not active or not isinstance(key, str):
                    continue
                if key.startswith("metric:") and (
                    key.endswith(":warn") or key.endswith(":critical")
                ):
                    parts = key.split(":")
                    if len(parts) >= 3:
                        metric = by_id.get(parts[1])
                        name = metric.name if metric else parts[1]
                        kind = parts[-1]
                        labels.append(f"{name} ({kind})")

            if labels:
                any_active = True
                blocks.append(
                    self._tr.t(
                        "cmd_alerts_device",
                        name=device.name,
                        host=device.host,
                        items=", ".join(labels),
                    )
                )
            else:
                blocks.append(
                    self._tr.t(
                        "cmd_alerts_device_ok",
                        name=device.name,
                        host=device.host,
                    )
                )

        if not any_active:
            blocks[0] = self._tr.t("cmd_alerts_none")
        return "\n".join(blocks)
