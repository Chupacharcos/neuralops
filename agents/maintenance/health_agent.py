"""Vigila NeuralOps sí mismo — auto-recuperación. Cada 10 min en polling."""
import subprocess
import logging
from graph.state import NeuralOpsState
from core.resource_manager import check_server_health
from core import telegram_bot, memory

logger = logging.getLogger(__name__)


async def health_agent(state: NeuralOpsState) -> NeuralOpsState:
    server = check_server_health()

    # Alert on resource thresholds
    if server["alerts"]:
        for alert in server["alerts"]:
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
    return state
