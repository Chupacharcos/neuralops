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

AGENTS = {
    # Mantenimiento
    "code_review":           "agents.maintenance.code_review:code_review",
    "test_runner":           "agents.maintenance.test_runner:test_runner",
    "dependency_watch":      "agents.maintenance.dependency_watch:dependency_watch",
    "backup_verifier":       "agents.maintenance.backup_verifier:backup_verifier",
    "model_drift":           "agents.maintenance.model_drift_detector:model_drift_detector",
    # Promoción
    "lead_scraper":          "agents.promotion.lead_scraper:lead_scraper",
    "lead_scorer":           "agents.promotion.lead_scorer:lead_scorer",
    "email_drafter":         "agents.promotion.email_drafter:email_drafter",
    "email_sender":          "agents.promotion.email_sender:email_sender",
    "content_creator":       "agents.promotion.content_creator:content_creator",
    # Inteligencia
    "seo_monitor":           "agents.intelligence.seo_monitor:seo_monitor",
    "project_onboarding":    "agents.intelligence.project_auto_onboarding:project_auto_onboarding",
    "project_evaluator":     "agents.intelligence.project_evaluator:evaluate_all_projects",
    "meta_agent":            "agents.intelligence.meta_agent:meta_agent",
    "portfolio_reorder":     "agents.intelligence.portfolio_reorder:portfolio_reorder",
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
    await func()
    logger.info(f"Agente completado: {agent_name}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Uso: python neuralops_cron.py <agente>")
        print(f"Agentes: {', '.join(AGENTS.keys())}")
        sys.exit(1)

    asyncio.run(run_agent(sys.argv[1]))
