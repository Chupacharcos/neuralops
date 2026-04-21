"""
Lead Scraper — encuentra empresas y emails reales por sector. Días lab 09:00.

Estrategia en cascada:
1. Hunter.io  (HUNTER_API_KEY)   — busca por dominio, muy preciso
2. Google CSE (GOOGLE_CSE_KEY)   — encuentra webs de empresas del sector
3. Fallback HTML                 — extrae mailto: de páginas encontradas

Sin API keys → solo fallback HTML (bajo rendimiento).

Para activar:
  HUNTER_API_KEY=xxx  (hunter.io → 25 búsquedas/mes gratis)
  GOOGLE_CSE_KEY=xxx + GOOGLE_CSE_CX=xxx (custom search engine)
"""
import os
import asyncio
import logging
import json
import re
import httpx
from bs4 import BeautifulSoup
from core import telegram_bot, memory
from core.leads_db import save_lead
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

PROJECTS_PATH = "/var/www/neuralops/projects.json"

HUNTER_KEY  = os.getenv("HUNTER_API_KEY", "")
GOOGLE_KEY  = os.getenv("GOOGLE_CSE_KEY", "")
GOOGLE_CX   = os.getenv("GOOGLE_CSE_CX", "")

# Consultas de búsqueda por sector para encontrar empresas relevantes
SECTOR_QUERIES = {
    "Inmobiliaria": [
        "agencia inmobiliaria Spain contacto",
        "promotora inmobiliaria España email",
        "tasacion vivienda empresa Spain",
    ],
    "Veterinaria / Mascotas": [
        "clinica veterinaria España contacto email",
        "hospital veterinario Spain",
    ],
    "Deportes": [
        "club deportivo profesional Spain contacto",
        "agencia scouting futbol España",
        "academia deportiva Spain email",
    ],
    "Fintech / E-commerce": [
        "startup fintech España contacto",
        "empresa pagos online Spain email",
        "ecommerce marketplace España",
    ],
    "Arquitectura / Interiorismo": [
        "estudio arquitectura España contacto",
        "interiorismo empresa Spain email",
    ],
    "Fiscal / Legal": [
        "gestoria asesoria fiscal España email contacto",
        "asesor fiscal autonomos Spain",
    ],
    "Salud": [
        "startup salud digital España",
        "clinica medica Spain contacto email",
    ],
    "Finanzas": [
        "fondo inversion Spain contacto",
        "broker finanzas España email",
    ],
}


def _load_projects() -> list:
    with open(PROJECTS_PATH) as f:
        return json.load(f)


def _extract_domain(url: str) -> str:
    """https://www.empresa.com/path → empresa.com"""
    url = re.sub(r"https?://(www\.)?", "", url)
    return url.split("/")[0]


def _extract_emails_from_html(html: str, source_url: str) -> list[dict]:
    """Extrae emails del HTML de una página."""
    leads = []
    soup = BeautifulSoup(html, "html.parser")

    # mailto: links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "mailto:" in href:
            email = href.replace("mailto:", "").split("?")[0].strip().lower()
            if "@" in email and "." in email and len(email) < 80:
                company = soup.find("title")
                company_name = company.get_text(strip=True)[:60] if company else "Empresa"
                leads.append({"email": email, "company": company_name, "source": source_url})

    # Emails en texto (regex)
    email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    text_emails = re.findall(email_pattern, soup.get_text())
    for email in set(text_emails[:5]):
        email = email.lower()
        if any(skip in email for skip in ["example", "test", "noreply", "no-reply", "info@info"]):
            continue
        if not any(l["email"] == email for l in leads):
            leads.append({"email": email, "company": "Extraído de texto", "source": source_url})

    return leads[:3]  # max 3 emails por página


async def _hunt_domain(domain: str, client: httpx.AsyncClient) -> list[dict]:
    """Hunter.io: busca emails asociados a un dominio."""
    if not HUNTER_KEY:
        return []
    try:
        resp = await client.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": HUNTER_KEY, "limit": 5},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json().get("data", {})
        emails = data.get("emails", [])
        company_name = data.get("organization", domain)
        return [
            {"email": e["value"], "company": company_name, "name": e.get("first_name", "") + " " + e.get("last_name", ""),
             "source": f"hunter.io/{domain}"}
            for e in emails if e.get("value") and e.get("confidence", 0) >= 70
        ]
    except Exception as e:
        logger.debug(f"[LeadScraper] Hunter {domain}: {e}")
        return []


async def _google_cse_search(query: str, client: httpx.AsyncClient) -> list[str]:
    """Google Custom Search: devuelve URLs de empresas del sector."""
    if not GOOGLE_KEY or not GOOGLE_CX:
        return []
    try:
        resp = await client.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": GOOGLE_KEY, "cx": GOOGLE_CX, "q": query, "num": 5},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        items = resp.json().get("items", [])
        return [item["link"] for item in items if item.get("link")]
    except Exception as e:
        logger.debug(f"[LeadScraper] Google CSE '{query}': {e}")
        return []


async def _scrape_url(url: str, client: httpx.AsyncClient) -> list[dict]:
    """Descarga una URL y extrae emails del HTML."""
    try:
        resp = await client.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NeuralOps/1.0)"},
            timeout=12,
            follow_redirects=True,
        )
        if resp.status_code == 200:
            return _extract_emails_from_html(resp.text, url)
    except Exception as e:
        logger.debug(f"[LeadScraper] scrape {url}: {e}")
    return []


async def lead_scraper():
    projects = [p for p in _load_projects() if p.get("activo", True)]
    new_leads = 0
    sectors_done = set()

    async with httpx.AsyncClient() as client:
        for project in projects:
            sector = project.get("sector", "")
            slug = project["slug"]

            if sector in sectors_done:
                continue
            sectors_done.add(sector)

            queries = SECTOR_QUERIES.get(sector, [])
            if not queries:
                logger.debug(f"[LeadScraper] sin queries para sector '{sector}'")
                continue

            urls_to_scrape: list[str] = []

            # 1. Google CSE si disponible
            for query in queries[:2]:
                found_urls = await _google_cse_search(query, client)
                urls_to_scrape.extend(found_urls[:3])

            # 2. Fallback: URLs hardcoded por sector si no hay CSE
            if not urls_to_scrape:
                fallback_urls = {
                    "Inmobiliaria": ["https://www.servihabitat.com/contacto", "https://www.engel-voelkers.com/es-es/contacto/"],
                    "Fintech / E-commerce": ["https://www.fintech.es/empresas/"],
                    "Deportes": ["https://www.rfef.es/contacto"],
                    "Veterinaria / Mascotas": [],
                    "Arquitectura / Interiorismo": ["https://www.coam.org/es/servicios/arquitectos"],
                }
                urls_to_scrape = fallback_urls.get(sector, [])

            # 3. Scrape URLs → extract emails
            for url in urls_to_scrape[:5]:
                raw_leads = await _scrape_url(url, client)

                # 4. Para cada dominio encontrado, enriquecer con Hunter.io
                for lead_data in raw_leads:
                    domain = _extract_domain(lead_data.get("source", ""))
                    hunter_leads = await _hunt_domain(domain, client)

                    if hunter_leads:
                        # Hunter encontró emails de calidad — usar esos
                        for hl in hunter_leads:
                            is_new = save_lead(
                                name=hl.get("name", ""),
                                company=hl["company"],
                                email=hl["email"],
                                sector=sector,
                                project_slug=slug,
                                source=hl["source"],
                            )
                            if is_new:
                                new_leads += 1
                    else:
                        # Sin Hunter → usar el email scrapeado directamente
                        is_new = save_lead(
                            name="",
                            company=lead_data.get("company", ""),
                            email=lead_data["email"],
                            sector=sector,
                            project_slug=slug,
                            source=lead_data["source"],
                        )
                        if is_new:
                            new_leads += 1

    if new_leads > 0:
        logger.info(f"[LeadScraper] {new_leads} nuevos leads guardados")
        await telegram_bot.send_alert(
            f"🎯 <b>LeadScraper</b> — {new_leads} nuevos leads encontrados\n"
            f"<i>Lead scorer los puntuará en 30 min</i>"
        )
        memory.log_event("lead_scraper", "scraped", {"new_leads": new_leads})
    else:
        logger.info(
            "[LeadScraper] Sin leads nuevos. "
            f"{'Hunter.io no configurado — añade HUNTER_API_KEY al .env' if not HUNTER_KEY else 'Sin resultados esta sesión'}"
        )

    return new_leads


if __name__ == "__main__":
    asyncio.run(lead_scraper())
