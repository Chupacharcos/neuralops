"""Checks all 15 demos every 2 min. 3 consecutive failures → Telegram alert."""
import json
import time
import logging
import httpx
from graph.state import NeuralOpsState
from core import telegram_bot, memory
from core.agent_status import report

logger = logging.getLogger(__name__)

PROJECTS_PATH = "/var/www/neuralops/projects.json"
TIMEOUT = 10.0
MAX_RESPONSE_TIME = 3.0
ALERT_THRESHOLD = 3


def _load_projects() -> list:
    with open(PROJECTS_PATH) as f:
        return json.load(f)


async def demo_watcher(state: NeuralOpsState) -> NeuralOpsState:
    projects = _load_projects()
    failures = state.get("demo_failures", {})

    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        for project in projects:
            slug = project["slug"]
            url = project["demo_url"]
            try:
                t0 = time.monotonic()
                resp = await client.get(url)
                elapsed = time.monotonic() - t0

                ok = resp.status_code == 200 and elapsed < MAX_RESPONSE_TIME
                if ok:
                    if failures.get(slug, 0) > 0:
                        logger.info(f"[DemoWatcher] {slug} recuperado")
                    failures[slug] = 0
                else:
                    reason = f"HTTP {resp.status_code}" if resp.status_code != 200 else f"lento ({elapsed:.1f}s)"
                    failures[slug] = failures.get(slug, 0) + 1
                    logger.warning(f"[DemoWatcher] {slug} fallo #{failures[slug]}: {reason}")

                    if failures[slug] >= ALERT_THRESHOLD:
                        await telegram_bot.send_alert(
                            f"🚨 <b>Demo caída</b>: {project['name']}\n"
                            f"URL: {url}\n"
                            f"Error: {reason}\n"
                            f"Fallos consecutivos: {failures[slug]}"
                        )
                        memory.log_event("demo_watcher", "demo_down", {"slug": slug, "reason": reason})

            except httpx.TimeoutException:
                failures[slug] = failures.get(slug, 0) + 1
                logger.warning(f"[DemoWatcher] {slug} timeout #{failures[slug]}")
                if failures[slug] >= ALERT_THRESHOLD:
                    await telegram_bot.send_alert(
                        f"🚨 <b>Demo timeout</b>: {project['name']}\n"
                        f"Sin respuesta en {TIMEOUT}s — {failures[slug]} fallos consecutivos"
                    )
            except Exception as e:
                logger.error(f"[DemoWatcher] {slug} error inesperado: {e}")

    state["demo_failures"] = failures
    total = len(projects)
    active_failures = {s: n for s, n in failures.items() if n > 0}
    if active_failures:
        worst = max(active_failures, key=active_failures.get)
        report("demo_watcher", f"⚠ {worst} — {active_failures[worst]} fallos | {total - len(active_failures)}/{total} OK", "warning")
    else:
        report("demo_watcher", f"{total}/{total} demos respondiendo OK", "ok")
    return state
