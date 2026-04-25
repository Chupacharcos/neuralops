"""Handler de chat para LeadScraper."""
import sqlite3
from datetime import datetime, timedelta


async def handle_intent(intent: str, args: dict) -> str:
    if intent == "status":
        c = sqlite3.connect("/var/www/neuralops/leads.db").cursor()
        week_ago = (datetime.now() - timedelta(days=7)).isoformat(sep=" ")
        c.execute("SELECT COUNT(*), MAX(created_at) FROM leads WHERE created_at >= ?", (week_ago,))
        n, last = c.fetchone()
        c.execute("SELECT sector, COUNT(*) FROM leads WHERE created_at >= ? GROUP BY sector ORDER BY 2 DESC", (week_ago,))
        by_sector = c.fetchall()

        lines = [
            "<b>🔍 LeadScraper</b>",
            f"Leads esta semana: <b>{n or 0}</b>",
            f"Última obtención: {last or 'nunca'}",
        ]
        if by_sector:
            lines.append("\n<b>Por sector:</b>")
            for s, count in by_sector:
                lines.append(f"  • {s}: {count}")
        return "\n".join(lines)

    if intent == "run":
        from agents.promotion.lead_scraper import lead_scraper
        await lead_scraper()
        c = sqlite3.connect("/var/www/neuralops/leads.db").cursor()
        c.execute("SELECT COUNT(*) FROM leads WHERE created_at >= datetime('now','-2 minutes')")
        new_n = c.fetchone()[0]
        return f"✅ Scraping ejecutado. <b>{new_n}</b> leads nuevos en la DB."

    return f"❓ Intent no soportado: <code>{intent}</code>"
