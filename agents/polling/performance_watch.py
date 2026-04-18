"""Pings all FastAPI /health endpoints every 5 min. Latency >2x avg → alert."""
import json
import time
import logging
import httpx
import subprocess
from graph.state import NeuralOpsState
from core import telegram_bot, memory

logger = logging.getLogger(__name__)

PROJECTS_PATH = "/var/www/neuralops/projects.json"
TIMEOUT = 15.0
LATENCY_WINDOW = 48       # keep 48 readings = 4h at 5min intervals
AUTO_RESTART_TIMEOUT = 900  # 15min without response → restart


def _load_projects() -> list:
    with open(PROJECTS_PATH) as f:
        return json.load(f)


def _get_service_name(port: int) -> str | None:
    port_to_service = {
        8088: "chatbot", 8089: "ml-inmobiliario", 8090: "ml-revalorizacion",
        8091: "ml-calidad-aire", 8100: "babymind", 8101: "metacoach",
        8102: "stem-splitter", 8004: "feliniai", 8001: "sports-engine",
        8002: "fraud-detector", 8003: "value-engine", 8005: "alphasignal",
        8006: "roomcraft", 8093: "apis-validador", 8094: "apis-facturas",
        8095: "apis-irpf",
    }
    return port_to_service.get(port)


async def performance_watch(state: NeuralOpsState) -> NeuralOpsState:
    projects = _load_projects()
    metrics = state.get("service_metrics", {})
    down_since = {}  # track when service went down

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for project in projects:
            port = project.get("api_port")
            health_url = project.get("health_url")
            if not health_url or not port:
                continue

            key = str(port)
            history = metrics.get(key, [])

            try:
                t0 = time.monotonic()
                resp = await client.get(health_url)
                latency = (time.monotonic() - t0) * 1000  # ms

                if resp.status_code == 200:
                    history.append(latency)
                    if len(history) > LATENCY_WINDOW:
                        history = history[-LATENCY_WINDOW:]
                    metrics[key] = history

                    # Check if latency is >2x the 24h average
                    if len(history) >= 10:
                        avg = sum(history[:-1]) / len(history[:-1])
                        if latency > avg * 2 and latency > 1000:
                            await telegram_bot.send_alert(
                                f"⚠️ <b>Latencia alta</b>: {project['name']}\n"
                                f"Actual: {latency:.0f}ms — Media 24h: {avg:.0f}ms\n"
                                f"Puerto: {port}"
                            )
                    down_since.pop(key, None)

            except (httpx.TimeoutException, httpx.ConnectError):
                logger.warning(f"[PerformanceWatch] Puerto {port} sin respuesta")

                # Auto-restart if down >15min (único caso de auto-acción)
                if key not in down_since:
                    down_since[key] = time.time()
                elif time.time() - down_since[key] > AUTO_RESTART_TIMEOUT:
                    svc = _get_service_name(port)
                    if svc:
                        try:
                            subprocess.run(["sudo", "systemctl", "restart", svc], timeout=30)
                            await telegram_bot.send_alert(
                                f"🔄 <b>Reinicio automático</b>: {project['name']}\n"
                                f"Servicio '{svc}' sin respuesta >15min — reiniciado"
                            )
                            memory.log_event("performance_watch", "auto_restart", {"service": svc, "port": port})
                            down_since.pop(key, None)
                        except Exception as e:
                            await telegram_bot.send_alert(
                                f"🚨 <b>Servicio caído sin respuesta</b>: {project['name']}\n"
                                f"Fallo al reiniciar '{svc}': {e}"
                            )
            except Exception as e:
                logger.error(f"[PerformanceWatch] Puerto {port}: {e}")

    state["service_metrics"] = metrics
    return state
