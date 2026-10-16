from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    get_cmd,
)

log = logging.getLogger(__name__)


class SnmpError(RuntimeError):
    pass


@dataclass(frozen=True)
class Sample:
    cpu: float
    memory_pct: float
    disk_pct: float
    memory_used_bytes: int
    memory_total_bytes: int
    disk_used_bytes: int
    disk_total_bytes: int
    uptime_ticks: int
    custom: dict[str, float]


class SnmpClient:
    def __init__(self) -> None:
        self._engine = SnmpEngine()

    async def get_raw(self, host: str, port: int, community: str, oid: str) -> str:
        target = await UdpTransportTarget.create((host, port), timeout=3, retries=0)
        error_indication, error_status, error_index, var_binds = await get_cmd(
            self._engine,
            CommunityData(community, mpModel=1),
            target,
            ContextData(),
            ObjectType(ObjectIdentity(oid)),
        )

        if error_indication:
            raise SnmpError(str(error_indication))
        if error_status:
            location = int(error_index) - 1 if error_index else 0
            hint = var_binds[location][0] if var_binds else "?"
            raise SnmpError(f"{error_status.prettyPrint()} at {hint}")

        if not var_binds:
            raise SnmpError(f"empty SNMP response for {oid}")

        value = var_binds[0][1].prettyPrint()
        if not value or "No Such" in value:
            raise SnmpError(f"SNMP returned no value for {oid}")
        return value

    async def get_number(self, host: str, port: int, community: str, oid: str) -> int:
        value = await self.get_raw(host, port, community, oid)
        match = re.search(r"-?\d+", value)
        if not match:
            raise SnmpError(f"cannot parse SNMP number from: {value}")
        return int(match.group())

    async def get_uptime_ticks(
        self, host: str, port: int, community: str, oid: str
    ) -> int:
        """
        Parse sysUpTime (hundredths of a second).

        Accepts:
          123456
          (123456) 0:20:34.56
          0:0:36:23.00
        """
        value = await self.get_raw(host, port, community, oid)

        match = re.search(r"\((\d+)\)", value)
        if match:
            return int(match.group(1))

        match = re.fullmatch(r"\s*(\d+)\s*", value)
        if match:
            return int(match.group(1))

        match = re.fullmatch(
            r"\s*(\d+):(\d+):(\d+):(\d+)(?:\.(\d+))?\s*",
            value,
        )
        if match:
            days = int(match.group(1))
            hours = int(match.group(2))
            minutes = int(match.group(3))
            seconds = int(match.group(4))
            fraction = match.group(5) or "0"
            hundredths = int(fraction[:2].ljust(2, "0"))
            total_seconds = days * 86400 + hours * 3600 + minutes * 60 + seconds
            return total_seconds * 100 + hundredths

        raise SnmpError(f"cannot parse sysUpTime: {value}")

    async def sample_device(self, device) -> Sample:
        oids = device.oids
        host, port, community = device.host, device.port, device.community

        uptime = await self.get_uptime_ticks(host, port, community, oids.uptime)
        cpu = float(await self.get_number(host, port, community, oids.cpu))

        mem_size = await self.get_number(host, port, community, oids.mem_size)
        mem_used = await self.get_number(host, port, community, oids.mem_used)
        mem_unit = await self.get_number(host, port, community, oids.mem_unit)

        disk_size = await self.get_number(host, port, community, oids.disk_size)
        disk_used = await self.get_number(host, port, community, oids.disk_used)
        disk_unit = await self.get_number(host, port, community, oids.disk_unit)

        if mem_size <= 0 or disk_size <= 0:
            raise SnmpError("invalid memory or disk capacity from SNMP")

        memory_total = mem_size * mem_unit
        memory_used = mem_used * mem_unit
        disk_total = disk_size * disk_unit
        disk_used_bytes = disk_used * disk_unit

        memory_pct = 100.0 * memory_used / memory_total
        disk_pct = 100.0 * disk_used_bytes / disk_total

        if not 0 <= cpu <= 100:
            raise SnmpError(f"unexpected CPU value: {cpu}")

        custom: dict[str, float] = {}
        for metric in device.metrics:
            try:
                raw = float(
                    await self.get_number(host, port, community, metric.oid)
                )
                custom[metric.id] = raw
            except Exception as exc:  # noqa: BLE001 — optional gauges must not kill poll
                log.warning(
                    "[%s] custom metric %s (%s) skipped: %s",
                    device.id,
                    metric.id,
                    metric.oid,
                    exc,
                )

        return Sample(
            cpu=cpu,
            memory_pct=memory_pct,
            disk_pct=disk_pct,
            memory_used_bytes=memory_used,
            memory_total_bytes=memory_total,
            disk_used_bytes=disk_used_bytes,
            disk_total_bytes=disk_total,
            uptime_ticks=uptime,
            custom=custom,
        )
