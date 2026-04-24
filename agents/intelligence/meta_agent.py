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

    # Guardar como system_context estructurado — todos los agentes lo leen
    system_ctx = {
        "week":            week,
        "report_text":     report,
        "emails_sent":     emails_sent,
        "leads_scraped":   leads_scraped,
        "code_issues":     code_issues,
        "test_failures":   test_failures,
        "demo_alerts":     demo_alerts,
        "model_drifts":    model_drifts,
        "social_mentions": social_mentions,
        "competitors":     competitors,
        "system_healthy":  demo_alerts < 3 and test_failures == 0,
        "generated_at":    datetime.now().isoformat(),
    }
    memory.upsert("system_context", "weekly_latest", report, system_ctx)
    memory.upsert("system_context", f"weekly_{week}", report, system_ctx)

    memory.log_event("meta_agent", "weekly_report", system_ctx)
    logger.info(f"[MetaAgent] informe semanal {week} generado y guardado en system_context")


async def daily_reporter():
    """Resumen diario a las 22:00 — lee agent_status.json y eventos del día."""
    import json as _json
    from pathlib import Path as _Path
    from core.agent_status import STATUS_FILE

    today = datetime.now().strftime("%d %b %Y")
    weekday = datetime.now().strftime("%A")
    weekday_es = {"Monday":"Lunes","Tuesday":"Martes","Wednesday":"Miércoles",
                  "Thursday":"Jueves","Friday":"Viernes","Saturday":"Sábado","Sunday":"Domingo"}.get(weekday, weekday)

    # Read live status
    status_data = {}
    if _Path(STATUS_FILE).exists():
        try:
            status_data = _json.loads(_Path(STATUS_FILE).read_text())
        except Exception:
            pass

    now_epoch = int(datetime.now().timestamp())
    active, alertas, stale = [], [], []
    for name, entry in status_data.items():
        age = now_epoch - entry.get("epoch", 0)
        level = entry.get("level", "info")
        if age > 3600:
            stale.append(name)
        elif level in ("warning", "error"):
            alertas.append(f"{name}: {entry.get('msg','')[:60]}")
        else:
            active.append(name)

    # Count today's events from ChromaDB
    today_prefix = datetime.now().strftime("%Y-%m-%d")
    all_events = memory.query("events", n_results=500)
    today_events = [e for e in all_events if e.get("metadata", {}).get("timestamp", "").startswith(today_prefix)]
    events_by_agent: dict[str, int] = {}
    for ev in today_events:
        ag = ev.get("metadata", {}).get("agent", "unknown")
        events_by_agent[ag] = events_by_agent.get(ag, 0) + 1

    # Build message
    lines = [f"📊 <b>Resumen NeuralOps — {weekday_es} {today}</b>\n"]

    if active:
        lines.append(f"✅ <b>Activos ({len(active)}):</b> {', '.join(active[:10])}")
    if alertas:
        lines.append(f"⚠️ <b>Con alertas:</b>")
        for a in alertas[:5]:
            lines.append(f"  · {a}")
    if stale:
        lines.append(f"😴 <b>Sin actividad reciente:</b> {', '.join(stale[:8])}")

    if events_by_agent:
        top = sorted(events_by_agent.items(), key=lambda x: x[1], reverse=True)[:5]
        lines.append(f"\n🔄 <b>Eventos hoy:</b> " + " · ".join(f"{ag}({n})" for ag, n in top))

    # Leads and emails from events
    leads_new = sum(e.get("metadata", {}).get("new_leads", 0) for e in today_events
                    if e.get("metadata", {}).get("agent") == "lead_scraper")
    emails_sent = len([e for e in today_events if e.get("metadata", {}).get("agent") == "email_sender"])
    if leads_new or emails_sent:
        lines.append(f"📬 Leads nuevos: {leads_new} · Emails enviados: {emails_sent}")

    await telegram_bot.send_alert("\n".join(lines))
    memory.log_event("meta_agent", "daily_report", {"date": today, "active": len(active), "alerts": len(alertas)})
    logger.info(f"[MetaAgent] Resumen diario enviado — {len(active)} activos, {len(alertas)} alertas")


if __name__ == "__main__":
    asyncio.run(meta_agent())
