"""Genera posts de LinkedIn sobre el mejor proyecto cada viernes 17h."""
import os
import asyncio
import logging
import json
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0.7)

PROJECTS_PATH = "/var/www/neuralops/projects.json"

POST_PROMPT = """Escribe un post de LinkedIn técnico y atractivo (máx 200 palabras) sobre este proyecto IA.

Proyecto: {name}
Sector: {sector}
Stack técnico clave: {keywords}
URL demo: {demo_url}

Requisitos:
- Empieza con una observación del sector o problema real (NO "Hoy quiero compartir")
- Explica qué hace el proyecto de forma técnica pero accesible
- Menciona el stack brevemente
- Termina con CTA a la demo
- Incluye 3-4 hashtags relevantes al final
- Tono: developer compartiendo trabajo real, no marketing

Responde SOLO con el post."""


def _load_projects():
    with open(PROJECTS_PATH) as f:
        return json.load(f)


def _pick_best_project(projects: list) -> dict:
    """Pick project with highest recent score from memory, fallback to random."""
    best = None
    best_score = -1
    for p in projects:
        scores = memory.query("project_scores", where={"project": p["slug"]}, n_results=1)
        if scores:
            score = scores[0]["metadata"].get("total", 0)
            if score > best_score:
                best_score = score
                best = p
    return best or projects[0]


async def content_creator():
    projects = _load_projects()
    project = _pick_best_project(projects)

    try:
        response = await llm.ainvoke(POST_PROMPT.format(
            name=project["name"],
            sector=project["sector"],
            keywords=", ".join(project.get("keywords", [])[:5]),
            demo_url=project["demo_url"],
        ))
        post = response.content.strip()

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
