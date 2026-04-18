"""Actualiza descripciones en el portfolio tras deploys aprobados."""
import os
import asyncio
import logging
import httpx
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0.3)

PORTFOLIO_API = "https://adrianmoreno-dev.com/api/projects"

UPDATER_PROMPT = """Eres un redactor técnico. Genera una descripción corta (2-3 frases) para la card de portfolio
de este proyecto basándote en las mejoras del CHANGELOG. Tono: técnico pero accesible, orientado a demostrar
capacidad técnica a reclutadores. NO uses emojis ni markdown.

Proyecto: {name}
CHANGELOG reciente:
{changelog}

Responde SOLO con la descripción, sin preámbulo."""


async def portfolio_updater(project_slug: str, changelog: str, project_name: str):
    """Genera descripción actualizada y solicita aprobación vía Telegram."""
    try:
        response = await llm.ainvoke(
            UPDATER_PROMPT.format(name=project_name, changelog=changelog)
        )
        new_description = response.content.strip()

        await telegram_bot.send_alert(
            f"📝 <b>PortfolioUpdater</b>: {project_name}\n\n"
            f"Nueva descripción generada:\n\n<i>{new_description}</i>\n\n"
            f"¿Aprobar actualización en el portfolio?"
        )
        memory.log_event("portfolio_updater", "description_generated", {
            "slug": project_slug, "description": new_description
        })

    except Exception as e:
        logger.error(f"[PortfolioUpdater] {project_slug}: {e}")


if __name__ == "__main__":
    asyncio.run(portfolio_updater("test-project", "feat: añadido endpoint de exportación PDF", "Test Project"))
