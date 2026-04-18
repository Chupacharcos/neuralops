"""Monitors Hacker News Show HN + Product Hunt RSS for competing tools."""
import logging
import feedparser
from graph.state import NeuralOpsState
from core import telegram_bot, memory

logger = logging.getLogger(__name__)

SHOW_HN_RSS = "https://hnrss.org/show?points=20"
PRODUCT_HUNT_RSS = "https://www.producthunt.com/feed"

SECTOR_KEYWORDS = {
    "inmobiliaria": ["real estate", "property", "housing price", "precio vivienda"],
    "veterinaria": ["cat allergy", "pet diagnosis", "veterinary ai", "dog health"],
    "arquitectura": ["room planner", "floor plan", "interior design ai", "3d room"],
    "finanzas": ["value betting", "sports arbitrage", "trading bot", "investment signal"],
    "musica": ["stem separation", "music ai", "demucs", "vocal remover"],
    "salud": ["baby development", "pediatric ai", "fitness coach ai", "hrv analysis"],
}


async def competitor_watcher(state: NeuralOpsState) -> NeuralOpsState:
    feeds = [
        ("Hacker News Show", SHOW_HN_RSS),
        ("Product Hunt", PRODUCT_HUNT_RSS),
    ]

    for feed_name, url in feeds:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                title = (entry.get("title") or "").lower()
                summary = (entry.get("summary") or "").lower()
                text = title + " " + summary

                for sector, keywords in SECTOR_KEYWORDS.items():
                    if any(kw in text for kw in keywords):
                        mem_id = f"comp_{entry.get('id', entry.link)}"
                        if memory.query("competitor_mentions", where={"source_id": mem_id}):
                            continue

                        await telegram_bot.send_alert(
                            f"🔭 <b>Competidor detectado</b> [{sector}]\n"
                            f"Fuente: {feed_name}\n"
                            f"<a href='{entry.link}'>{entry.title}</a>"
                        )
                        memory.upsert("competitor_mentions", mem_id, entry.title,
                                      {"source_id": mem_id, "sector": sector})
                        break
        except Exception as e:
            logger.error(f"[CompetitorWatcher] {feed_name}: {e}")

    return state
