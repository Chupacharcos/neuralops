"""
Lead Scraper — encuentra empresas y emails reales por sector.
Estrategia: lista curada de dominios por sector → Hunter.io domain-search → save_lead
Sin Hunter.io: extrae emails directamente del HTML de las empresas.
Corre días laborables 09:00.
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
from core.agent_status import report
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

PROJECTS_PATH = "/var/www/neuralops/projects.json"
HUNTER_KEY    = os.getenv("HUNTER_API_KEY", "")

# Dominios curados por sector — empresas españolas reales con email público
# Hunter.io puede buscar emails por dominio directamente (no necesita Google CSE)
SECTOR_DOMAINS: dict[str, list[dict]] = {
    "Inmobiliaria": [
        {"domain": "servihabitat.com",     "company": "Servihabitat"},
        {"domain": "cbre.es",              "company": "CBRE España"},
        {"domain": "savills.es",           "company": "Savills España"},
        {"domain": "colliers.com",         "company": "Colliers España"},
        {"domain": "engel-voelkers.com",   "company": "Engel & Völkers"},
        {"domain": "jll.es",               "company": "JLL España"},
        {"domain": "gesvalt.es",           "company": "Gesvalt"},
        {"domain": "tinsa.es",             "company": "Tinsa"},
        {"domain": "solvia.es",            "company": "Solvia"},
        {"domain": "fotocasa.es",          "company": "Fotocasa"},
    ],
    "Veterinaria / Mascotas": [
        {"domain": "vetpharma.es",         "company": "VetPharma"},
        {"domain": "vetanimal.com",        "company": "Vet Animal"},
        {"domain": "anicura.es",           "company": "Anicura España"},
        {"domain": "ivcpetcare.com",       "company": "IVC Evidensia España"},
        {"domain": "hospitalvetbarcelona.com", "company": "Hospital Vet Barcelona"},
        {"domain": "mundivets.com",        "company": "Mundivets"},
    ],
    "Deportes": [
        {"domain": "rfef.es",              "company": "RFEF"},
        {"domain": "laliga.es",            "company": "LaLiga"},
        {"domain": "mediapro.com",         "company": "Mediapro"},
        {"domain": "wyscout.com",          "company": "Wyscout"},
        {"domain": "statsperform.com",     "company": "Stats Perform"},
        {"domain": "instat.football",      "company": "InStat Football"},
        {"domain": "hudl.com",             "company": "Hudl"},
    ],
    "Fintech / E-commerce": [
        {"domain": "paycomet.com",         "company": "Paycomet"},
        {"domain": "sipay.es",             "company": "Sipay"},
        {"domain": "monei.com",            "company": "MONEI"},
        {"domain": "flywire.com",          "company": "Flywire"},
        {"domain": "unnax.com",            "company": "Unnax"},
        {"domain": "fintonic.com",         "company": "Fintonic"},
        {"domain": "aplazame.com",         "company": "Aplazame"},
    ],
    "Arquitectura / Interiorismo": [
        {"domain": "coam.org",             "company": "COAM"},
        {"domain": "actiu.com",            "company": "Actiu"},
        {"domain": "kettal.com",           "company": "Kettal"},
        {"domain": "b720.com",             "company": "B720 Fermín Vázquez"},
        {"domain": "batlle-roig.com",      "company": "Batlle i Roig"},
        {"domain": "acxt.net",             "company": "ACXT Arquitectos"},
    ],
    "Salud": [
        {"domain": "quironsalud.es",       "company": "Quirónsalud"},
        {"domain": "sanitas.es",           "company": "Sanitas"},
        {"domain": "ribera.es",            "company": "Ribera Salud"},
        {"domain": "hospitalpicassent.com","company": "Hospital Ribera"},
        {"domain": "doctoralia.es",        "company": "Doctoralia"},
        {"domain": "mediktor.com",         "company": "Mediktor"},
    ],
    "Finanzas": [
        {"domain": "openbank.es",          "company": "Openbank"},
        {"domain": "indexacapital.com",    "company": "Indexa Capital"},
        {"domain": "selfbank.es",          "company": "Self Bank"},
        {"domain": "myinvestor.es",        "company": "MyInvestor"},
        {"domain": "finanbest.com",        "company": "Finanbest"},
        {"domain": "magallanesfunds.com",  "company": "Magallanes Value"},
    ],
    "IA / ML": [
        {"domain": "adevinta.com",         "company": "Adevinta"},
        {"domain": "cabify.com",           "company": "Cabify Tech"},
        {"domain": "databeacon.aero",      "company": "Databeacon"},
        {"domain": "bdataxplore.com",      "company": "BDataXplore"},
        {"domain": "sngular.com",          "company": "Sngular"},
        {"domain": "threadsol.com",        "company": "ThreadSol"},
    ],
}


def _load_projects() -> list:
    with open(PROJECTS_PATH) as f:
        return json.load(f)


async def _hunt_domain(domain: str, company: str, client: httpx.AsyncClient) -> list[dict]:
    """Hunter.io domain-search: devuelve emails con confidence >= 60."""
    if not HUNTER_KEY:
        return []
    try:
        resp = await client.get(
            "https://api.hunter.io/v2/domain-search",
            params={"domain": domain, "api_key": HUNTER_KEY, "limit": 3},
            timeout=15,
        )
        if resp.status_code == 429:
            logger.warning("[LeadScraper] Hunter.io rate limit alcanzado")
            return []
        if resp.status_code != 200:
            logger.debug(f"[LeadScraper] Hunter {domain}: HTTP {resp.status_code}")
            return []
        data = resp.json().get("data", {})
        emails = data.get("emails", [])
        org = data.get("organization") or company
        return [
            {
                "email":   e["value"],
                "company": org,
                "name":    f"{e.get('first_name','')} {e.get('last_name','')}".strip(),
                "source":  f"hunter.io/{domain}",
            }
            for e in emails
            if e.get("value") and e.get("confidence", 0) >= 60
        ]
    except Exception as e:
        logger.debug(f"[LeadScraper] Hunter {domain}: {e}")
        return []


async def _scrape_contact_page(domain: str, company: str, client: httpx.AsyncClient) -> list[dict]:
    """Fallback: busca emails en la página de contacto de la empresa."""
    leads = []
    for path in ["/contacto", "/contact", "/contactar", "/sobre-nosotros", "/"]:
        url = f"https://{domain}{path}"
        try:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; NeuralOps/1.0; +https://adrianmoreno-dev.com)"},
                timeout=10,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            # mailto links
            for a in soup.find_all("a", href=re.compile(r"mailto:")):
                email = a["href"].replace("mailto:", "").split("?")[0].strip().lower()
                if "@" in email and "." in email and len(email) < 80:
                    if not any(skip in email for skip in ["noreply", "no-reply", "example", "test"]):
                        if not any(l["email"] == email for l in leads):
                            leads.append({"email": email, "company": company, "source": url, "name": ""})
            if leads:
                break
        except Exception:
            continue
    return leads[:2]


async def lead_scraper():
    projects = {p["slug"]: p for p in _load_projects() if p.get("activo", True)}
    new_leads = 0
    domains_tried = 0

    # Check Hunter.io quota first
    if HUNTER_KEY:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    "https://api.hunter.io/v2/account",
                    params={"api_key": HUNTER_KEY}
                )
                if r.status_code == 200:
                    acct = r.json().get("data", {})
                    remaining = acct.get("requests", {}).get("searches", {}).get("available", 0)
                    logger.info(f"[LeadScraper] Hunter.io búsquedas disponibles: {remaining}")
                    if remaining <= 0:
                        logger.warning("[LeadScraper] Hunter.io sin búsquedas disponibles este mes")
                        report("lead_scraper", "Hunter.io: sin búsquedas disponibles este mes", "warning")
                        return 0
        except Exception as e:
            logger.warning(f"[LeadScraper] No se pudo comprobar quota Hunter.io: {e}")

    async with httpx.AsyncClient() as client:
        for sector, domain_list in SECTOR_DOMAINS.items():
            # Encontrar proyecto del portfolio que corresponda a este sector
            project = next(
                (p for p in projects.values() if p.get("sector") == sector),
                next(iter(projects.values()), None)  # fallback: primer proyecto
            )
            if not project:
                continue
            slug = project["slug"]

            for entry in domain_list:
                domain  = entry["domain"]
                company = entry["company"]

                # Skip si ya tenemos lead de este dominio
                existing = memory.query("email_drafts", n_results=5)
                domain_done = any(domain in str(d.get("metadata", {})) for d in existing)
                if domain_done:
                    continue

                domains_tried += 1
                found: list[dict] = []

                # 1. Hunter.io (más preciso)
                if HUNTER_KEY:
                    found = await _hunt_domain(domain, company, client)
                    await asyncio.sleep(1.2)  # rate limit

                # 2. Fallback: scraping de contacto
                if not found:
                    found = await _scrape_contact_page(domain, company, client)

                for lead_data in found:
                    is_new = save_lead(
                        name=lead_data.get("name", ""),
                        company=lead_data["company"],
                        email=lead_data["email"],
                        sector=sector,
                        project_slug=slug,
                        source=lead_data.get("source", domain),
                    )
                    if is_new:
                        new_leads += 1
                        logger.info(f"[LeadScraper] nuevo lead: {lead_data['email']} ({company}) → {slug}")

                # Max 8 dominios por ejecución para respetar la cuota mensual
                if domains_tried >= 8:
                    break
            if domains_tried >= 8:
                break

    if new_leads > 0:
        logger.info(f"[LeadScraper] {new_leads} nuevos leads guardados ({domains_tried} dominios)")
        await telegram_bot.send_alert(
            f"🎯 <b>LeadScraper</b> — {new_leads} nuevos leads\n"
            f"Dominios consultados: {domains_tried}\n"
            f"<i>LeadScorer los puntuará en 30min</i>"
        )
        memory.log_event("lead_scraper", "scraped", {"new_leads": new_leads, "domains": domains_tried})
        report("lead_scraper", f"{new_leads} leads nuevos | {domains_tried} dominios procesados", "ok")
    else:
        logger.info(f"[LeadScraper] 0 nuevos leads — {domains_tried} dominios consultados")
        report("lead_scraper", f"Sin leads nuevos | {domains_tried} dominios consultados", "info")

    return new_leads


if __name__ == "__main__":
    asyncio.run(lead_scraper())
