"""MetaAgent — Análisis semanal, actualiza pesos Bandit y genera informe. Lunes 08:00."""
import os
import asyncio
import logging
from datetime import datetime, timedelta
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from core.bandit import get_stats, update_reward
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0.3)

META_PROMPT = """Eres el MetaAgent de NeuralOps. Analiza los datos de la semana y genera un informe ejecutivo.

DATOS DE LA SEMANA:
- Emails enviados: {emails_sent}
- Leads scrapeados: {leads_scraped}
- Issues CodeReview creados: {code_issues}
- Tests fallando: {test_failures}
- Demos caídas (alertas): {demo_alerts}
- Proyectos con drift de modelo: {model_drifts}
- Menciones en redes: {social_mentions}
- Competidores detectados: {competitors}

BANDIT STATS (tasa de respuesta por estrategia email):
{bandit_stats}

Genera un informe semanal breve con:
1. Resumen del estado del sistema (2-3 frases)
2. Métrica más destacada (positiva o negativa)
3. Top 2 acciones recomendadas para la semana
4. Tendencia a vigilar

Máx 200 palabras. Tono directo y técnico."""


def _count_events_last_week(agent: str, event: str = None) -> int:
    events = memory.query("events", where={"agent": agent}, n_results=200)
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    return sum(
        1 for e in events
        if e["metadata"].get("timestamp", "") >= week_ago
        and (event is None or e["document"] == event)
    )


async def meta_agent():
    # Collect weekly stats
    emails_sent = _count_events_last_week("email_sender")
    leads_scraped = _count_events_last_week("lead_scraper")
    code_issues = _count_events_last_week("code_review", "issue_created")
    test_failures = _count_events_last_week("test_runner", "tests_failed")
    demo_alerts = _count_events_last_week("demo_watcher", "demo_down")
    model_drifts = _count_events_last_week("model_drift")
    social_mentions = len(memory.query("social_mentions", n_results=100))
    competitors = _count_events_last_week("competitor_watcher")

    # Bandit stats for email strategies
    email_bandit = get_stats("email_templates")
    bandit_text = "\n".join(
        f"  {arm}: {data['pulls']} envíos, {data['avg_reward']*100:.0f}% respuesta"
        for arm, data in email_bandit.items()
    ) if email_bandit else "  Sin datos aún (pocos emails enviados)"

    # Generate report with LLM
    response = await llm.ainvoke(META_PROMPT.format(
        emails_sent=emails_sent,
        leads_scraped=leads_scraped,
        code_issues=code_issues,
        test_failures=test_failures,
        demo_alerts=demo_alerts,
        model_drifts=model_drifts,
        social_mentions=social_mentions,
        competitors=competitors,
        bandit_stats=bandit_text,
    ))
    report = response.content.strip()

    week = datetime.now().strftime("%Y-W%V")
    await telegram_bot.send_alert(
        f"🧠 <b>MetaAgent — Informe Semanal {week}</b>\n\n"
        f"{report}\n\n"
        f"<i>Métricas: {emails_sent} emails · {leads_scraped} leads · "
        f"{demo_alerts} alertas demo · {social_mentions} menciones</i>"
    )

    memory.log_event("meta_agent", "weekly_report", {
        "week": week,
        "emails_sent": emails_sent,
        "leads_scraped": leads_scraped,
        "demo_alerts": demo_alerts,
    })
    logger.info(f"[MetaAgent] informe semanal {week} generado")


if __name__ == "__main__":
    asyncio.run(meta_agent())
