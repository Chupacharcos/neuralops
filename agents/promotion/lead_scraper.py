"""Scraping de leads de directorios públicos por sector. Días lab 09:00."""
import asyncio
import logging
import httpx
from bs4 import BeautifulSoup
from core import telegram_bot, memory
from core.leads_db import save_lead
from dotenv import load_dotenv
import json

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

# Fuentes por sector (públicas, scraping respetuoso)
SCRAPING_SOURCES = {
    "Inmobiliaria": [
        {"url": "https://www.idealista.com/noticias/", "type": "rss_fallback"},
    ],
    "Veterinaria / Mascotas": [
        {"url": "https://www.doctoralia.es/medicos/veterinarios", "type": "html"},
    ],
    "Deportes": [
        {"url": "https://www.laliga.com/noticias", "type": "html"},
    ],
    "Fintech / E-commerce": [
        {"url": "https://www.fintech.es/empresas/", "type": "html"},
    ],
}

# Load projects for scraping context
PROJECTS_PATH = "/var/www/neuralops/projects.json"


def _load_projects():
    with open(PROJECTS_PATH) as f:
        return json.load(f)


async def _scrape_page(url: str, client: httpx.AsyncClient) -> list[dict]:
    """Extract company/contact info from a public directory page."""
    leads = []
    try:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return leads

        soup = BeautifulSoup(resp.text, "html.parser")

        # Generic extraction: find links with emails or contact info
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "mailto:" in href:
                email = href.replace("mailto:", "").split("?")[0].strip()
                if "@" in email and "." in email:
                    company = a.get_text(strip=True) or "Empresa"
                    leads.append({"email": email, "company": company, "source": url})

    except Exception as e:
        logger.debug(f"[LeadScraper] {url}: {e}")

    return leads


async def lead_scraper():
    projects = _load_projects()
    new_leads = 0

    async with httpx.AsyncClient() as client:
        for project in projects:
            sector = project["sector"]
            slug = project["slug"]
            sources = SCRAPING_SOURCES.get(sector, [])

            for source in sources:
                raw_leads = await _scrape_page(source["url"], client)
                for lead_data in raw_leads[:10]:  # max 10 per source
                    is_new = save_lead(
                        name="",
                        company=lead_data.get("company", ""),
                        email=lead_data["email"],
                        sector=sector,
                        project_slug=slug,
                        source=source["url"],
                    )
                    if is_new:
                        new_leads += 1

    if new_leads > 0:
        logger.info(f"[LeadScraper] {new_leads} nuevos leads guardados")
        memory.log_event("lead_scraper", "scraped", {"new_leads": new_leads})
    else:
        logger.info("[LeadScraper] Sin leads nuevos esta sesión")

    return new_leads


if __name__ == "__main__":
    asyncio.run(lead_scraper())
