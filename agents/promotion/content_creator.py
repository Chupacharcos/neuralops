"""Genera posts de LinkedIn sobre el mejor proyecto cada viernes 17h."""
import os
import asyncio
import logging
import json
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from core.shared_context import get_best_project_to_promote, load_system_context
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="meta-llama/llama-4-scout-17b-16e-instruct", api_key=os.getenv("GROQ_API_KEY"), temperature=0.7)

PROJECTS_PATH = "/var/www/neuralops/projects.json"

POST_PROMPT = """Escribe un post de LinkedIn técnico y atractivo (máx 200 palabras) sobre este proyecto IA.

Proyecto: {name}
Sector: {sector}
Stack técnico clave: {keywords}
URL demo: {demo_url}
Contexto del sistema: {system_note}

Requisitos:
- Empieza con una observación del sector o problema real (NO "Hoy quiero compartir")
- Explica qué hace el proyecto de forma técnica pero accesible
- Menciona el stack brevemente
- Termina con CTA a la demo
- Incluye 3-4 hashtags relevantes al final
- Tono: developer compartiendo trabajo real, no marketing
- Si hay contexto del sistema relevante, incorpóralo sutilmente

Responde SOLO con el post."""


def _load_projects():
    with open(PROJECTS_PATH) as f:
        return json.load(f)


async def content_creator():
    projects = [p for p in _load_projects() if p.get("activo", True)]

    # Leer contexto del sistema (MetaAgent + RecommendationRouter)
    ctx = load_system_context()

    # Proyectos ya publicados en LinkedIn (evitar repetir)
    posted = {r["metadata"].get("slug") for r in memory.query("events", n_results=100)
              if r["document"] == "post_generated"}

    project = get_best_project_to_promote(projects, already_posted=posted)
    if not project:
        logger.warning("[ContentCreator] Sin proyectos disponibles")
        return

    # Nota contextual para el LLM
    system_note = "sistema estable"
    if not ctx["system_healthy"]:
        system_note = "sistema con incidencias recientes — mantener tono sobrio"
    elif ctx["promotion_priority"] and project["slug"] in ctx["promotion_priority"]:
        system_note = "proyecto recomendado por análisis automático para mayor visibilidad"

    try:
        response = await llm.ainvoke(POST_PROMPT.format(
            name=project["name"],
            sector=project["sector"],
            keywords=", ".join(project.get("keywords", [])[:5]),
            demo_url=project["demo_url"],
            system_note=system_note,
        ))
        post = response.content.strip()

        # Desactivar prioridad de promoción una vez usado
        if project["slug"] in ctx["promotion_priority"]:
            memory.upsert("promotion_priority", f"promo_{project['slug']}",
                          f"Promocionado en LinkedIn", {
                              "slug": project["slug"], "status": "used",
                              "used_at": __import__("datetime").datetime.now().isoformat()
                          })

        await telegram_bot.send_alert(
            f"📱 <b>ContentCreator — Post LinkedIn</b>\n"
            f"Proyecto: {project['name']}\n\n"
            f"{post}\n\n"
            f"<i>Publica este post en LinkedIn cuando quieras</i>"
        )
        memory.log_event("content_creator", "post_generated", {"slug": project["slug"]})
        logger.info(f"[ContentCreator] post generado para {project['name']}")

    except Exception as e:
        logger.error(f"[ContentCreator]: {e}")


if __name__ == "__main__":
    asyncio.run(content_creator())
