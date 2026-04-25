"""Monitoriza RAM, disco y swap del servidor. Corre cada 15 min via cron."""
import asyncio
import logging
import shutil
import psutil
from core import telegram_bot, memory
from core.agent_status import report

logger = logging.getLogger(__name__)

RAM_ALERT_MB   = 400
SWAP_ALERT_PCT = 80
DISK_ALERT_PCT = 85


async def health_agent():
    vm   = psutil.virtual_memory()
    sw   = psutil.swap_memory()
    disk = psutil.disk_usage("/")

    ram_free_mb  = vm.available // (1024 * 1024)
    swap_pct     = sw.percent
    disk_pct     = disk.percent

    alerts = []
    if ram_free_mb < RAM_ALERT_MB:
        alerts.append(f"🔴 RAM libre: {ram_free_mb} MB (umbral {RAM_ALERT_MB} MB)")
    if swap_pct > SWAP_ALERT_PCT:
        alerts.append(f"🟡 Swap: {swap_pct:.0f}% usado (umbral {SWAP_ALERT_PCT}%)")
    if disk_pct > DISK_ALERT_PCT:
        alerts.append(f"🔴 Disco: {disk_pct:.0f}% usado (umbral {DISK_ALERT_PCT}%)")

    if alerts:
        await telegram_bot.send_alert(
            "⚠️ <b>HealthAgent — Alerta de sistema</b>\n\n" + "\n".join(alerts)
        )

    status_msg = (
        f"RAM: {ram_free_mb} MB libre | "
        f"Swap: {swap_pct:.0f}% | "
        f"Disco: {disk_pct:.0f}%"
    )
    report("health_agent", status_msg, "error" if alerts else "ok")
    memory.log_event("health_agent", "checked", {
        "ram_free_mb": ram_free_mb,
        "swap_pct": round(swap_pct, 1),
        "disk_pct": round(disk_pct, 1),
        "alerts": alerts,
    })


if __name__ == "__main__":
    asyncio.run(health_agent())
