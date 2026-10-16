from __future__ import annotations

from mikrotik_bot.i18n import Translator
from mikrotik_bot.models import CustomMetric, DeviceConfig
from mikrotik_bot.snmp_client import Sample


def format_pct(value: float) -> str:
    return f"{value:.1f}%"


def format_bytes(num: int | float) -> str:
    value = float(num)
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if abs(value) < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def format_usage(pct: float, used_bytes: int, total_bytes: int) -> str:
    """e.g. 42.3% (1.2 GB / 2.8 GB)"""
    return (
        f"{format_pct(pct)} "
        f"({format_bytes(used_bytes)} / {format_bytes(total_bytes)})"
    )


def format_uptime(ticks: int) -> str:
    total_seconds = ticks // 100
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_metric_value(metric: CustomMetric, value: float) -> str:
    if metric.type == "percent":
        return format_pct(value)
    unit = f" {metric.unit}" if metric.unit else ""
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value))}{unit}"
    return f"{value:.2f}{unit}"


def metric_emoji(metric: CustomMetric | None, metric_id: str) -> str:
    key = f"{metric_id} {(metric.unit if metric else '')} {(metric.name if metric else '')}".lower()
    if "temp" in key or key.rstrip().endswith(" c") or "°" in key:
        return "🌡️"
    if "fan" in key and "state" in key:
        return "🌀"
    if "fan" in key or "rpm" in key:
        return "🌬️"
    if "psu" in key or "power" in key or "volt" in key:
        return "🔌"
    if "disk" in key or "storage" in key:
        return "💾"
    if "cpu" in key:
        return "🧠"
    if "mem" in key or "ram" in key:
        return "🧮"
    return "📌"


def build_status_message(
    tr: Translator, device: DeviceConfig, sample: Sample
) -> str:
    lines = [
        tr.t("status_header", name=device.name, host=device.host),
        tr.t("status_line_cpu", value=format_pct(sample.cpu)),
        tr.t(
            "status_line_memory",
            value=format_usage(
                sample.memory_pct,
                sample.memory_used_bytes,
                sample.memory_total_bytes,
            ),
        ),
        tr.t(
            "status_line_disk",
            value=format_usage(
                sample.disk_pct,
                sample.disk_used_bytes,
                sample.disk_total_bytes,
            ),
        ),
        tr.t("status_line_uptime", value=format_uptime(sample.uptime_ticks)),
    ]
    by_id = {m.id: m for m in device.metrics}
    for metric_id, value in sample.custom.items():
        metric = by_id.get(metric_id)
        label = metric.name if metric else metric_id
        rendered = format_metric_value(metric, value) if metric else str(value)
        line = tr.t("status_line_metric", label=label, value=rendered)
        lines.append(f"{metric_emoji(metric, metric_id)} {line}")
    return "\n".join(lines)
