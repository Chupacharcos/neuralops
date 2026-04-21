"""
Publica threads de X/Twitter sobre proyectos del portfolio.
Jueves 17:00h España (15:00 UTC) — un proyecto por semana, en cola por prioridad.
"""
import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from core.twitter_client import is_configured, post_thread
from core.shared_context import get_best_project_to_promote, load_system_context
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0.75)

PROJECTS_PATH = "/var/www/neuralops/projects.json"
PORTFOLIO_BASE = "https://adrianmoreno-dev.com"

THREAD_PROMPT = """Escribe un thread de X/Twitter de 4 tweets sobre este proyecto de IA/ML.

Proyecto: {name}
Sector: {sector}
Stack técnico clave: {keywords}
URL demo: {demo_url}
Descripción: {description}

REGLAS ESTRICTAS:
- Tweet 1 (gancho): empieza con un dato impactante o pregunta provocadora del sector. Máx 260 chars.
- Tweet 2 (qué hace): explica qué resuelve el proyecto y cómo. Técnico pero claro. Máx 270 chars.
- Tweet 3 (stack/métricas): menciona tecnologías clave y una métrica real. Máx 270 chars.
- Tweet 4 (CTA): enlace a la demo + call to action. Incluye 3-4 hashtags. Máx 275 chars.
- Tono: desarrollador compartiendo trabajo real. Sin emojis de manos ni corazones.
- Tweet 1 NO empieza con "He construido" ni "Hoy presento".
- El número de tweet va al inicio (1/, 2/, 3/, 4/).

Responde SOLO con JSON:
{{"tweets": ["tweet1", "tweet2", "tweet3", "tweet4"]}}"""


def _load_projects() -> list:
    if not os.path.exists(PROJECTS_PATH):
        return []
    with open(PROJECTS_PATH) as f:
        return json.load(f)


async def twitter_publisher():
    if not is_configured():
        logger.warning("[TwitterPublisher] credenciales no configuradas — saltando")
        await telegram_bot.send_alert(
            "⚠️ <b>TwitterPublisher</b>: credenciales no configuradas.\n"
            "Añade TWITTER_API_KEY, TWITTER_API_SECRET y TWITTER_ACCESS_SECRET en .env"
        )
        return

    all_projects = [p for p in _load_projects() if p.get("activo", True)]
    if not all_projects:
        logger.info("[TwitterPublisher] no hay proyectos en projects.json")
        return

    # Proyectos ya twiteados (para evitar repetir en el mismo ciclo)
    posted = {r["id"] for r in memory.query("twitter_posted", n_results=100)}

    # Usar shared_context para elegir por prioridad del router
    project = get_best_project_to_promote(all_projects, already_posted=posted)
    if not project:
        logger.info("[TwitterPublisher] cola vacía")
        return

    # Leer contexto del sistema para ajustar el tono
    ctx = load_system_context()
    is_priority = project["slug"] in ctx.get("promotion_priority", [])

    demo_url = project.get("demo_url") or f"{PORTFOLIO_BASE}/demo/{project['slug']}"

    try:
        response = await llm.ainvoke(THREAD_PROMPT.format(
            name=project["name"],
            sector=project.get("sector", "IA / ML"),
            keywords=", ".join(project.get("keywords", [])[:5]),
            demo_url=demo_url,
            description=project.get("description", ""),
        ))

        raw = response.content.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
        tweets = data.get("tweets", [])

        if len(tweets) < 4:
            raise ValueError(f"El LLM devolvió {len(tweets)} tweets, se esperaban 4")

        result = post_thread(tweets)

        if result.get("success"):
            memory.upsert("twitter_posted", project["slug"], datetime.now(timezone.utc).isoformat())
            # Desactivar prioridad de promoción una vez twiteado
            if is_priority:
                memory.upsert("promotion_priority", f"promo_{project['slug']}",
                              "Promocionado en Twitter", {
                                  "slug": project["slug"], "status": "used",
                                  "used_at": datetime.now(timezone.utc).isoformat()
                              })
            memory.log_event("twitter_publisher", "thread_posted", {
                "slug": project["slug"],
                "tweet_ids": result["tweet_ids"],
            })

            preview = "\n\n".join(f"<code>{t[:120]}...</code>" for t in tweets)
            await telegram_bot.send_alert(
                f"🐦 <b>TwitterPublisher</b> — thread publicado\n"
                f"Proyecto: {project['name']}\n\n"
                f"{preview}"
            )
            logger.info(f"[TwitterPublisher] thread publicado: {project['slug']}")
        else:
            raise RuntimeError(result.get("error", "error desconocido"))

    except Exception as e:
        logger.error(f"[TwitterPublisher] error: {e}")
        await telegram_bot.send_alert(
            f"❌ <b>TwitterPublisher</b> falló\n"
            f"Proyecto: {project.get('name', '?')}\n"
            f"Error: {e}"
        )


if __name__ == "__main__":
    asyncio.run(twitter_publisher())
