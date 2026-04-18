"""Puntúa leads con LLM (0-100) basado en fit con el proyecto. Tras scraper."""
import os
import asyncio
import logging
import json
from langchain_groq import ChatGroq
from core.leads_db import get_leads, update_lead
from core import memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0)

PROJECTS_PATH = "/var/www/neuralops/projects.json"

SCORER_PROMPT = """Evalúa si esta empresa podría beneficiarse del producto IA.
Responde SOLO con un JSON: {{"score": 0-100, "reason": "1 frase"}}

Empresa: {company}
Sector empresa: {sector}
Producto IA: {product_name} — {product_description}
Sector producto: {product_sector}

Criterios:
- 80-100: empresa usa tech similar, sector exacto, tamaño mediano-grande
- 60-79: sector relacionado, podría beneficiarse claramente
- 40-59: posible interés pero no obvio
- 0-39: sector muy diferente o empresa demasiado pequeña"""


def _load_projects():
    with open(PROJECTS_PATH) as f:
        return json.load(f)


async def lead_scorer():
    projects = {p["slug"]: p for p in _load_projects()}
    new_leads = get_leads(status="new", limit=20)

    if not new_leads:
        logger.info("[LeadScorer] Sin leads nuevos que puntuar")
        return

    for lead in new_leads:
        project = projects.get(lead["project_slug"])
        if not project:
            continue

        try:
            response = await llm.ainvoke(SCORER_PROMPT.format(
                company=lead.get("company", "desconocida"),
                sector=lead["sector"],
                product_name=project["name"],
                product_description=project.get("keywords", []),
                product_sector=project["sector"],
            ))

            content = response.content.strip()
            # Extract JSON even if LLM adds extra text
            start = content.find("{")
            end = content.rfind("}") + 1
            if start >= 0:
                data = json.loads(content[start:end])
                score = int(data.get("score", 0))
                reason = data.get("reason", "")
                update_lead(lead["email"], score=score, status="scored")
                memory.log_event("lead_scorer", "scored", {"email": lead["email"], "score": score})
                logger.info(f"[LeadScorer] {lead['email']}: score={score} — {reason}")

        except Exception as e:
            logger.error(f"[LeadScorer] {lead['email']}: {e}")
            update_lead(lead["email"], status="scored", score=0)


if __name__ == "__main__":
    asyncio.run(lead_scorer())
