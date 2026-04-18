"""Monitoriza posicionamiento web via Google Search Console API. Miércoles semanal."""
import os
import asyncio
import logging
import httpx
import json
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0)

SITE_URL = "https://adrianmoreno-dev.com/"
SITE_URL_ENCODED = "https%3A%2F%2Fadrianmoreno-dev.com%2F"
GSC_TOKEN_PATH = "/var/www/neuralops/.gsc_token.json"  # OAuth token file

SEO_PROMPT = """Analiza estos datos de Google Search Console y sugiere mejoras concretas de SEO.
Para cada oportunidad detectada, da el cambio exacto en title o meta description.

Datos de keywords con impresiones pero CTR bajo (<3%):
{keywords_data}

Páginas analizadas:
{pages}

Responde con máx 5 sugerencias concretas, formato:
- Página: /ruta/pagina
  Problema: [descripción]
  Fix: [nuevo title o meta description exacto]"""


async def _refresh_token_if_needed(token: dict) -> dict:
    """Renueva el access_token usando el refresh_token si ha expirado."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": token["client_id"],
                "client_secret": token["client_secret"],
                "refresh_token": token["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code == 200:
            new_data = resp.json()
            token["access_token"] = new_data["access_token"]
            with open(GSC_TOKEN_PATH, "w") as f:
                json.dump(token, f, indent=2)
            logger.info("[SEOMonitor] token renovado correctamente")
    return token


async def _get_search_console_data(token: dict) -> dict | None:
    """Query Google Search Console API for search analytics."""
    from datetime import date, timedelta
    end_date = date.today().isoformat()
    start_date = (date.today() - timedelta(days=28)).isoformat()

    headers = {"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json"}
    payload = {
        "startDate": start_date,
        "endDate": end_date,
        "dimensions": ["query", "page"],
        "rowLimit": 100,
    }

    async with httpx.AsyncClient() as client:
        gsc_url = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{SITE_URL_ENCODED}/searchAnalytics/query"
        resp = await client.post(gsc_url, headers=headers, json=payload, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 401:
            logger.info("[SEOMonitor] token expirado — renovando")
            token = await _refresh_token_if_needed(token)
            headers["Authorization"] = f"Bearer {token['access_token']}"
            resp = await client.post(gsc_url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                return resp.json()
        logger.error(f"[SEOMonitor] GSC API error {resp.status_code}: {resp.text[:200]}")
    return None


def _find_low_ctr_opportunities(data: dict) -> list[dict]:
    """Find queries with impressions > 10 but CTR < 3%."""
    opportunities = []
    for row in data.get("rows", []):
        impressions = row.get("impressions", 0)
        ctr = row.get("ctr", 0)
        query = row["keys"][0] if row.get("keys") else ""
        page = row["keys"][1] if len(row.get("keys", [])) > 1 else ""

        if impressions >= 10 and ctr < 0.03:
            opportunities.append({
                "query": query,
                "page": page,
                "impressions": impressions,
                "ctr": round(ctr * 100, 1),
                "position": round(row.get("position", 0), 1),
            })
    return sorted(opportunities, key=lambda x: x["impressions"], reverse=True)[:10]


async def seo_monitor():
    if not os.path.exists(GSC_TOKEN_PATH):
        logger.info("[SEOMonitor] token OAuth no encontrado — saltando")
        return

    with open(GSC_TOKEN_PATH) as f:
        token = json.load(f)

    data = await _get_search_console_data(token)
    if not data:
        logger.warning("[SEOMonitor] no se pudieron obtener datos de GSC")
        return

    opportunities = _find_low_ctr_opportunities(data)
    if not opportunities:
        logger.info("[SEOMonitor] sin oportunidades de mejora esta semana")
        return

    keywords_text = "\n".join(
        f"- '{o['query']}' → {o['page']} | {o['impressions']} impresiones | CTR {o['ctr']}% | pos {o['position']}"
        for o in opportunities
    )
    pages = list({o["page"] for o in opportunities})

    response = await llm.ainvoke(SEO_PROMPT.format(keywords_data=keywords_text, pages="\n".join(pages)))
    suggestions = response.content.strip()

    await telegram_bot.send_alert(
        f"🔍 <b>SEOMonitor — Informe semanal</b>\n\n"
        f"Oportunidades detectadas: {len(opportunities)}\n\n"
        f"{suggestions[:1000]}"
    )
    memory.log_event("seo_monitor", "report_generated", {"opportunities": len(opportunities)})


if __name__ == "__main__":
    asyncio.run(seo_monitor())
