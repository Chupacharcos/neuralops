"""Detecta proyectos nuevos en el portfolio y los registra en NeuralOps. Cada hora."""
import os
import asyncio
import logging
import json
import httpx
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from core.github_api import create_issue, get_repo_info
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0)

PORTFOLIO_API = "https://adrianmoreno-dev.com/api/projects"
KNOWN_PROJECTS_PATH = "/var/www/neuralops/projects.json"
GH_USER = os.getenv("GITHUB_USERNAME")
GH_TOKEN = os.getenv("GITHUB_TOKEN")

INFER_PROMPT = """Analiza este nuevo proyecto de portfolio IA y extrae su configuración para NeuralOps.

Proyecto: {name}
Descripción: {description}
Demo URL: {demo_url}

Responde SOLO con JSON válido:
{{
  "sector": "sector principal del proyecto",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
  "has_model": true/false,
  "github_repo": "nombre-del-repo-en-github",
  "scraping_sources": ["URL fuente leads 1", "URL fuente leads 2"]
}}"""


def _load_known() -> list:
    if not os.path.exists(KNOWN_PROJECTS_PATH):
        return []
    with open(KNOWN_PROJECTS_PATH) as f:
        return json.load(f)


def _save_projects(projects: list):
    with open(KNOWN_PROJECTS_PATH, "w") as f:
        json.dump(projects, f, indent=2, ensure_ascii=False)


async def _infer_config(project: dict) -> dict:
    """Use LLM to infer sector, keywords and scraping sources for new project."""
    try:
        response = await llm.ainvoke(INFER_PROMPT.format(
            name=project.get("name", ""),
            description=project.get("description", ""),
            demo_url=project.get("demo_url", ""),
        ))
        content = response.content.strip()
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0:
            return json.loads(content[start:end])
    except Exception as e:
        logger.error(f"[ProjectAutoOnboarding] infer_config error: {e}")
    return {}


async def _create_github_repo_if_missing(repo_name: str) -> bool:
    """Create GitHub repo with standard structure if it doesn't exist."""
    info = await get_repo_info(repo_name)
    if info and "id" in info:
        return False  # Already exists

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.github.com/user/repos",
            headers={
                "Authorization": f"token {GH_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={
                "name": repo_name,
                "description": f"AI project — adrianmoreno-dev.com",
                "private": False,
                "auto_init": True,
            },
        )
        return resp.status_code == 201


async def project_auto_onboarding():
    # Fetch current portfolio projects
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(PORTFOLIO_API)
            if resp.status_code != 200:
                logger.warning(f"[ProjectAutoOnboarding] Portfolio API returned {resp.status_code}")
                return
            portfolio_projects = resp.json()
        except Exception as e:
            logger.error(f"[ProjectAutoOnboarding] API error: {e}")
            return

    known = _load_known()
    known_slugs = {p["slug"] for p in known}

    for project in portfolio_projects:
        slug = project.get("slug", "")
        if not slug or slug in known_slugs:
            continue

        logger.info(f"[ProjectAutoOnboarding] Nuevo proyecto detectado: {slug}")

        # Infer configuration with LLM
        config = await _infer_config(project)

        # Build new project entry
        new_entry = {
            "slug": slug,
            "name": project.get("name", slug),
            "demo_url": f"https://adrianmoreno-dev.com/demo/{slug}",
            "health_url": None,
            "api_port": None,
            "sector": config.get("sector", "General"),
            "has_model": config.get("has_model", False),
            "github_repo": config.get("github_repo", slug),
            "keywords": config.get("keywords", []),
        }

        # Create GitHub repo if missing
        repo_created = await _create_github_repo_if_missing(new_entry["github_repo"])

        # Add to known projects
        known.append(new_entry)
        _save_projects(known)
        known_slugs.add(slug)

        memory.upsert("known_projects", slug, new_entry["name"], new_entry)

        # Notify via Telegram for approval
        await telegram_bot.send_alert(
            f"🆕 <b>Proyecto nuevo detectado</b>\n"
            f"Nombre: {new_entry['name']}\n"
            f"Slug: <code>{slug}</code>\n"
            f"Sector inferido: {new_entry['sector']}\n"
            f"Keywords: {', '.join(new_entry['keywords'][:3])}\n"
            f"Repo GitHub: {'✅ creado' if repo_created else '⚠️ ya existía'} — "
            f"github.com/{GH_USER}/{new_entry['github_repo']}\n\n"
            f"DemoWatcher y PerformanceWatch lo monitorizarán en el próximo ciclo."
        )

    if not any(p["slug"] not in known_slugs for p in portfolio_projects):
        logger.debug("[ProjectAutoOnboarding] Sin proyectos nuevos")


if __name__ == "__main__":
    asyncio.run(project_auto_onboarding())
