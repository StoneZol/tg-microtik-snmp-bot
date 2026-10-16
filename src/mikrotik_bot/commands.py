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

    async def run_forever(self) -> None:
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

    async def _handle_message(self, token: str, message: dict[str, Any]) -> None:
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        chat_type = chat.get("type") or "private"
        if not chat_id or chat_type != "private":
            # Only private DMs for ACL + subscriber tracking for now
            return

        from_user = message.get("from") or {}
        user_id = from_user.get("id")
        user_id_int = int(user_id) if user_id is not None else None
        username = from_user.get("username")

        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
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

        # Allowed → mark bot as active for this chat
        self._subscribers.touch(
            token,
            chat_id,
            user_id=user_id_int,
            username=username,
        )

        head, _, rest = text.partition(" ")
        command = head.split("@", 1)[0].lower()
        arg = rest.strip()
        scoped = devices_for_user(
            self._devices, token, user_id_int, username
        )

        if command in {"/start", "/help"}:
            await self._telegram.send_message(
                token, chat_id, self._tr.t("cmd_help")
            )
            return

        if command == "/devices":
            if not scoped:
                await self._telegram.send_message(
                    token, chat_id, self._tr.t("cmd_no_devices")
                )
                return
            lines = [self._tr.t("cmd_devices_header")]
            for device in scoped:
                lines.append(
                    self._tr.t(
                        "cmd_devices_line",
                        id=device.id,
                        name=device.name,
                        host=device.host,
                    )
                )
            await self._telegram.send_message(token, chat_id, "\n".join(lines))
            return

        if command == "/status":
            if not scoped:
                await self._telegram.send_message(
                    token, chat_id, self._tr.t("cmd_no_devices")
                )
                return

            if arg:
                device = next((d for d in scoped if d.id == arg), None)
                if device is None:
                    await self._telegram.send_message(
                        token,
                        chat_id,
                        self._tr.t("cmd_unknown_device", id=arg),
                    )
                    return
                targets = [device]
            else:
                targets = scoped

            chunks: list[str] = []
            for device in targets:
                monitor = self._monitors[device.id]
                chunks.append(await monitor.status_text())
            await self._telegram.send_message(
                token, chat_id, "\n\n".join(chunks)
            )
            return
