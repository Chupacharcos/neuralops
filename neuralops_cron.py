"""Lanzador de agentes por crontab. Uso: python neuralops_cron.py <agente>"""
import asyncio
import sys
import os
import logging

sys.path.insert(0, "/var/www/neuralops")
from dotenv import load_dotenv
load_dotenv("/var/www/neuralops/.env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/var/www/neuralops/logs/cron.log"),
    ],
)
logger = logging.getLogger("neuralops_cron")
# Prevent child loggers from duplicating to root handler
logging.getLogger().handlers = []  # root has no handlers; basicConfig already set ours
from core.agent_status import report

AGENTS = {
    # Mantenimiento
    "control_agent":         "agents.maintenance.control_agent:control_agent",
    "code_review":           "agents.maintenance.code_review:code_review",
    "test_runner":           "agents.maintenance.test_runner:test_runner",
    "dependency_watch":      "agents.maintenance.dependency_watch:dependency_watch",
    "backup_verifier":       "agents.maintenance.backup_verifier:backup_verifier",
    "model_drift":           "agents.maintenance.model_drift_detector:model_drift_detector",
    "github_sync":           "agents.maintenance.github_sync:github_sync",
    "error_repair":          "agents.maintenance.error_repair:error_repair",
    # Promoción
    "lead_scraper":          "agents.promotion.lead_scraper:lead_scraper",
    "lead_scorer":           "agents.promotion.lead_scorer:lead_scorer",
    "email_drafter":         "agents.promotion.email_drafter:email_drafter",
    "email_sender":          "agents.promotion.email_sender:email_sender",
    "content_creator":       "agents.promotion.content_creator:content_creator",
    "twitter_publisher":     "agents.promotion.twitter_publisher:twitter_publisher",
    # Inteligencia
    "project_builder":       "agents.intelligence.project_builder:project_builder",
    "seo_monitor":           "agents.intelligence.seo_monitor:seo_monitor",
    "project_onboarding":    "agents.intelligence.project_auto_onboarding:project_auto_onboarding",
    "project_evaluator":       "agents.intelligence.project_evaluator:evaluate_all_projects",
    "meta_agent":              "agents.intelligence.meta_agent:meta_agent",
    "daily_reporter":          "agents.intelligence.meta_agent:daily_reporter",
    "portfolio_reorder":       "agents.intelligence.portfolio_reorder:portfolio_reorder",
    "recommendation_router":   "agents.intelligence.recommendation_router:recommendation_router",
}


async def run_agent(agent_name: str):
    if agent_name not in AGENTS:
        print(f"Agente desconocido: {agent_name}")
        print(f"Disponibles: {', '.join(AGENTS.keys())}")
        sys.exit(1)

    module_path, func_name = AGENTS[agent_name].rsplit(":", 1)
    module = __import__(module_path, fromlist=[func_name])
    func = getattr(module, func_name)

    logger.info(f"Iniciando agente: {agent_name}")
    report(agent_name, "Ejecutando ciclo...", "info")
    await func()
    logger.info(f"Agente completado: {agent_name}")
    # Si el agente no sobreescribió su status, marcar como completado
    import time as _time
    from pathlib import Path as _Path
    import json as _json
    _sf = _Path("/var/www/neuralops/agent_status.json")
    if _sf.exists():
        try:
            _data = _json.loads(_sf.read_text())
            from core.agent_status import AGENT_DISPLAY
            _disp = AGENT_DISPLAY.get(agent_name, agent_name)
            entry = _data.get(_disp, {})
            if entry.get("msg") == "Ejecutando ciclo...":
                report(agent_name, f"Ciclo completado — {_time.strftime('%H:%M')}", "ok")
        except Exception:
            pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Uso: python neuralops_cron.py <agente>")
        print(f"Agentes: {', '.join(AGENTS.keys())}")
        sys.exit(1)

    asyncio.run(run_agent(sys.argv[1]))
