"""Vigila NeuralOps sí mismo — auto-recuperación. Cada 10 min en polling."""
import subprocess
import logging
import time
from graph.state import NeuralOpsState
from core.resource_manager import check_server_health
from core import telegram_bot, memory
from core.agent_status import report

logger = logging.getLogger(__name__)

ALERT_COOLDOWN_S = 3600  # mismo alert no se repite antes de 1h


def _alert_key(alert: str) -> str:
    """Normaliza la alerta como clave para deduplicar."""
    if "RAM" in alert:
        return "ram_high"
    if "CPU" in alert:
        return "cpu_high"
    if "Disco" in alert:
        return "disk_high"
    if "Swap" in alert:
        return "swap_high"
    return alert[:40]


async def health_agent(state: NeuralOpsState) -> NeuralOpsState:
    server = check_server_health()

    # Alert on resource thresholds — with cooldown to avoid spam
    if server["alerts"]:
        now = time.time()
        for alert in server["alerts"]:
            key = _alert_key(alert)
            rows = memory.query("health_agent_cooldown")
            last_ts = next((float(r["document"]) for r in rows if r["id"] == key), 0)
            if now - last_ts < ALERT_COOLDOWN_S:
                logger.debug(f"[HealthAgent] alerta '{key}' suprimida (cooldown)")
                continue
            memory.upsert("health_agent_cooldown", key, str(now))
            await telegram_bot.send_alert(f"⚠️ <b>HealthAgent alerta</b>\n{alert}")
            memory.log_event("health_agent", "resource_alert", {"alert": alert})

    # Clean old logs if disk > 80%
    if server["disk_pct"] > 80:
        try:
            subprocess.run(
                ["find", "/var/www/neuralops/logs", "-name", "*.log", "-mtime", "+7", "-delete"],
                timeout=10
            )
            logger.info("[HealthAgent] logs antiguos eliminados (disco >80%)")
        except Exception as e:
            logger.error(f"[HealthAgent] error limpiando logs: {e}")

    logger.debug(f"[HealthAgent] RAM {server['ram_free_mb']}MB libres, CPU {server['cpu_pct']}%, Disk {server['disk_pct']}%")
    level = "warning" if server["alerts"] else "ok"
    report("health_agent",
           f"RAM libre {server['ram_free_mb']}MB | CPU {server['cpu_pct']}% | Disco {server['disk_pct']}%",
           level)
    return state
