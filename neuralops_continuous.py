"""NeuralOps — Proceso continuo de polling con LangGraph routing. Lanzado por systemd."""
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
from graph.neuralops_graph import run_graph_cycle
from core.resource_manager import check_server_health
from core import telegram_bot
from agents.polling.email_tracker import email_tracker
from agents.polling.analytics_parser import analytics_parser
from agents.polling.social_listener import social_listener
from agents.polling.competitor_watcher import competitor_watcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/www/neuralops/logs/neuralops.log"),
    ],
)
logger = logging.getLogger("neuralops")

# Agents that run on fixed intervals (don't benefit from LLM routing)
SCHEDULE = [
    (email_tracker,      900,  "Link clicks"),
    (analytics_parser,   900,  "Active sessions"),
    (social_listener,    3600, "Social mentions"),
    (competitor_watcher, 3600, "Market signals"),
]

# How often the LangGraph router cycle runs (LLM decides what to check)
GRAPH_INTERVAL = 60


async def run_polling():
    state: NeuralOpsState = default_state()
    last_run = {agent.__name__: datetime.min for agent, _, _ in SCHEDULE}
    last_graph_run = datetime.min

    await telegram_bot.send_alert(
        "🟢 <b>NeuralOps arrancado</b> — LangGraph routing activo\n"
        "Router LLM decidirá qué verificar en cada ciclo"
    )
    logger.info("NeuralOps polling iniciado con LangGraph routing")

    while True:
        now = datetime.now()

        server = check_server_health()
        low_memory = server["ram_free_mb"] < 500
        if low_memory:
            logger.warning(f"RAM baja ({server['ram_free_mb']}MB) — pausando agentes no críticos")
            await asyncio.sleep(60)
            continue

        # LangGraph cycle — router LLM decides: demo_check, service_check, health_check, response_check
        elapsed_graph = (now - last_graph_run).total_seconds()
        if elapsed_graph >= GRAPH_INTERVAL:
            try:
                logger.info(f"[Graph] ciclo #{state.get('cycle_count', 0) + 1} — LLM routing")
                state = await run_graph_cycle(state, max_steps=6)
                last_graph_run = now
            except Exception as e:
                logger.error(f"[Graph] error en ciclo: {e}", exc_info=True)
                state.setdefault("last_errors", {})["graph"] = str(e)

        # Fixed-interval agents
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
