"""Searches Hacker News + Reddit + Google Alerts RSS for project mentions."""
import logging
import feedparser
import httpx
from graph.state import NeuralOpsState
from core import telegram_bot, memory

logger = logging.getLogger(__name__)

HN_SEARCH = "https://hnrss.org/newest?q={query}&points=10"
GOOGLE_ALERTS_FEEDS: list[str] = []  # Add Google Alerts RSS URLs here


async def social_listener(state: NeuralOpsState) -> NeuralOpsState:
    keywords = [
        "adrianmoreno-dev", "FeliniAI", "RoomCraft AI", "AlphaSignal",
        "BabyMind", "MetaCoach", "Stem Splitter AI",
    ]

    for kw in keywords:
        try:
            feed = feedparser.parse(HN_SEARCH.format(query=kw.replace(" ", "+")))
            for entry in feed.entries[:3]:
                mem_id = f"hn_{entry.get('id', entry.link)}"
                existing = memory.query("social_mentions", where={"source_id": mem_id})
                if existing:
                    continue

                await telegram_bot.send_alert(
                    f"📣 <b>Mención en Hacker News</b>\n"
                    f"Keyword: {kw}\n"
                    f"<a href='{entry.link}'>{entry.title}</a>"
                )
                memory.upsert("social_mentions", mem_id, entry.title, {"source_id": mem_id, "keyword": kw})
        except Exception as e:
            logger.error(f"[SocialListener] {kw}: {e}")

    return state
