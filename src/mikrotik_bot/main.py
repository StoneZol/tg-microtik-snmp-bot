from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from mikrotik_bot.commands import CommandListener
from mikrotik_bot.config import ConfigError, load_devices, load_settings, unique_bot_tokens
from mikrotik_bot.hooks import run_hook_server
from mikrotik_bot.i18n import Translator
from mikrotik_bot.monitor import DeviceMonitor
from mikrotik_bot.snmp_client import SnmpClient
from mikrotik_bot.state import StateStore
from mikrotik_bot.subscribers import SubscriberStore
from mikrotik_bot.telegram import TelegramClient


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


async def run() -> None:
    settings = load_settings()
    devices = load_devices(settings.config_dir)
    translator = Translator(settings.locales_dir, settings.lang)
    state_store = StateStore(settings.state_dir)
    subscribers = SubscriberStore(Path(settings.state_dir) / "subscribers.sqlite")
    snmp = SnmpClient()
    telegram = TelegramClient()

    devices_by_id = {device.id: device for device in devices}
    monitors = {
        device.id: DeviceMonitor(
            device=device,
            snmp=snmp,
            telegram=telegram,
            state_store=state_store,
            subscribers=subscribers,
            translator=translator,
        )
        for device in devices
    }

    tokens = unique_bot_tokens(devices)
    listener = CommandListener(
        telegram=telegram,
        translator=translator,
        devices=devices,
        monitors=monitors,
        subscribers=subscribers,
        tokens=tokens,
    )

    logging.getLogger(__name__).info(
        "Loaded %s device(s), %s bot token(s), lang=%s, hook_port=%s",
        len(devices),
        len(tokens),
        settings.lang,
        settings.hook_port,
    )

    tasks = [
        listener.run_forever(),
        *(monitor.run_forever() for monitor in monitors.values()),
    ]
    if settings.hook_port > 0:
        tasks.append(
            run_hook_server(
                host=settings.hook_host,
                port=settings.hook_port,
                devices=devices_by_id,
                monitors=monitors,
                translator=translator,
            )
        )
    else:
        logging.getLogger(__name__).info("Hook listener disabled (HOOK_PORT=0)")

    try:
        await asyncio.gather(*tasks)
    finally:
        await telegram.aclose()


def main() -> None:
    setup_logging()
    try:
        asyncio.run(run())
    except ConfigError as exc:
        logging.getLogger(__name__).error("Config error: %s", exc)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
