"""NeuralOps — Proceso continuo de polling. Lanzado por systemd."""
import asyncio
import logging
import sys
import os
from datetime import datetime

sys.path.insert(0, "/var/www/neuralops")
os.environ.setdefault("DOTENV_PATH", "/var/www/neuralops/.env")

from dotenv import load_dotenv
load_dotenv("/var/www/neuralops/.env")

from graph.state import NeuralOpsState, default_state
from core.resource_manager import check_server_health
from core import telegram_bot
from agents.polling.demo_watcher import demo_watcher
from agents.polling.performance_watch import performance_watch
from agents.polling.response_handler import response_handler
from agents.polling.email_tracker import email_tracker
from agents.polling.analytics_parser import analytics_parser
from agents.polling.social_listener import social_listener
from agents.polling.competitor_watcher import competitor_watcher
from agents.maintenance.health_agent import health_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/www/neuralops/logs/neuralops.log"),
    ],
)
logger = logging.getLogger("neuralops")

# (agent_fn, interval_seconds, description)
SCHEDULE = [
    (demo_watcher,       120,  "Demo errors"),
    (performance_watch,  300,  "Service health"),
    (response_handler,   300,  "Email inbox"),
    (email_tracker,      900,  "Link clicks"),
    (analytics_parser,   900,  "Active sessions"),
    (social_listener,    3600, "Social mentions"),
    (competitor_watcher, 3600, "Market signals"),
    (health_agent,       600,  "Server health"),
]


async def run_polling():
    state: NeuralOpsState = default_state()
    last_run = {agent.__name__: datetime.min for agent, _, _ in SCHEDULE}

    await telegram_bot.send_alert("🟢 <b>NeuralOps arrancado</b> — polling continuo activo\n7 agentes listos")
    logger.info("NeuralOps polling iniciado")

    while True:
        now = datetime.now()

        # Pause non-critical agents if RAM < 500MB free
        server = check_server_health()
        low_memory = server["ram_free_mb"] < 500
        if low_memory:
            logger.warning(f"RAM baja ({server['ram_free_mb']}MB) — pausando agentes no críticos")
            await asyncio.sleep(60)
            continue

        for agent_fn, interval_sec, desc in SCHEDULE:
            elapsed = (now - last_run[agent_fn.__name__]).total_seconds()
            if elapsed < interval_sec:
                continue

            try:
                logger.info(f"[{agent_fn.__name__}] ejecutando — {desc}")
                state = await agent_fn(state)
                last_run[agent_fn.__name__] = now
            except Exception as e:
                logger.error(f"[{agent_fn.__name__}] error: {e}", exc_info=True)
                state.setdefault("last_errors", {})[agent_fn.__name__] = str(e)

        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(run_polling())
