"""ProjectEvaluator — Agente 23. Evalúa los 15 proyectos en 8 dimensiones. 1er lunes del mes."""
import os
import asyncio
import logging
import json
import re
import subprocess
from datetime import datetime
import httpx
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0)

PROJECTS_PATH = "/var/www/neuralops/projects.json"
GH_USER = os.getenv("GITHUB_USERNAME")
GH_TOKEN = os.getenv("GITHUB_TOKEN")
GH_HEADERS = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}

# Weights as per document section 8
WEIGHTS = {"d1": 0.20, "d2": 0.20, "d3": 0.15, "d4": 0.15, "d5": 0.15, "d6": 0.10, "d7": 0.05}


def _load_projects() -> list:
    with open(PROJECTS_PATH) as f:
        return json.load(f)


async def _score_d1_usage(project: dict) -> float:
    """D1 — Métricas de uso real (Nginx logs). Peso 20%."""
    try:
        slug = project["slug"]
        result = subprocess.run(
            ["grep", f"/demo/{slug}", "/var/log/nginx/access.log"],
            capture_output=True, text=True, timeout=10
        )
        visits = len(result.stdout.splitlines())
        # Score 0-100 based on visits: 0=0, 50=500+
        return min(100, visits / 5)
    except Exception:
        return 0.0


async def _score_d2_technical(project: dict, client: httpx.AsyncClient) -> float:
    """D2 — Calidad técnica (issues abiertos, latencia API). Peso 20%."""
    score = 80.0  # base score
    repo = project.get("github_repo")

    if repo:
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{GH_USER}/{repo}/issues",
                headers=GH_HEADERS, params={"state": "open", "per_page": 50}, timeout=10
            )
            if resp.status_code == 200:
                open_issues = len([i for i in resp.json() if "pull_request" not in i])
                score -= min(40, open_issues * 5)
        except Exception:
            pass

    health_url = project.get("health_url")
    if health_url:
        try:
            import time
            t0 = time.monotonic()
            resp = await client.get(health_url, timeout=5)
            latency_ms = (time.monotonic() - t0) * 1000
            if resp.status_code != 200:
                score -= 20
            elif latency_ms > 2000:
                score -= 10
        except Exception:
            score -= 30

    return max(0, score)


async def _score_d3_model(project: dict, client: httpx.AsyncClient) -> float | None:
    """D3 — Calidad del modelo ML (solo proyectos con modelo). Peso 15%."""
    if not project.get("has_model"):
        return None

    # Check last drift event in memory
    events = memory.query("model_drift", where={"slug": project["slug"]}, n_results=1)
    if events:
        score_val = events[0]["metadata"].get("score")
        baseline = {"prediccion-precio-inmobiliario": 0.88, "prediccion-calidad-aire": 0.416,
                    "fraud-detector": 0.9999, "sports-engine": 0.72, "feliniai": 0.9675}.get(project["slug"])
        if score_val and baseline:
            ratio = score_val / baseline
            return min(100, ratio * 80)
    return 60.0  # default if no drift data


async def _score_d4_market(project: dict, client: httpx.AsyncClient) -> float:
    """D4 — Posición en el mercado (competidores detectados). Peso 15%."""
    score = 70.0
    # Check competitor mentions from memory
    mentions = memory.query("competitor_mentions", where={"sector": project["sector"]}, n_results=10)
    score -= min(30, len(mentions) * 3)  # more competitors = lower score
    return max(0, score)


async def _score_d5_promotion(project: dict) -> float:
    """D5 — Impacto en promoción (clicks emails, menciones redes). Peso 15%."""
    score = 0.0

    # Check email tracker events
    emails = memory.query("emails_sent_log", n_results=100)
    project_emails = [e for e in emails if project["slug"] in e.get("document", "")]
    score += min(50, len(project_emails) * 10)

    # Check social mentions
    mentions = memory.query("social_mentions", where={"keyword": project["name"]}, n_results=10)
    score += min(50, len(mentions) * 15)

    return min(100, score)


async def _score_d6_availability(project: dict, client: httpx.AsyncClient) -> float:
    """D6 — Disponibilidad y fiabilidad. Peso 10%."""
    score = 100.0
    slug = project["slug"]

    # Check demo failure history
    events = memory.query("events", where={"agent": "demo_watcher"}, n_results=50)
    failures = [e for e in events if slug in e.get("document", "")]
    score -= min(50, len(failures) * 10)

    return max(0, score)


async def _score_d7_growth(project: dict, client: httpx.AsyncClient) -> float:
    """D7 — Potencial de crecimiento (GitHub stars, forks). Peso 5%."""
    repo = project.get("github_repo")
    if not repo:
        return 50.0
    try:
        resp = await client.get(f"https://api.github.com/repos/{GH_USER}/{repo}",
                                headers=GH_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            stars = data.get("stargazers_count", 0)
            forks = data.get("forks_count", 0)
            return min(100, stars * 5 + forks * 10)
    except Exception:
        pass
    return 30.0


RECOMMENDATIONS_PROMPT = """Basándote en estos scores de un proyecto IA de portfolio, da 1-2 acciones concretas y prioritarias.

Proyecto: {name}
D1 Uso: {d1}/100 | D2 Técnica: {d2}/100 | D3 Modelo: {d3} | D4 Mercado: {d4}/100
D5 Promo: {d5}/100 | D6 Disponibilidad: {d6}/100 | D7 Crecimiento: {d7}/100
Score total: {total}/100

Responde con 1-2 acciones específicas (máx 1 línea cada una). Sin preámbulo."""


async def evaluate_all_projects():
    projects = _load_projects()
    scores = []
    month = datetime.now().strftime("%Y-%m")

    async with httpx.AsyncClient() as client:
        for project in projects:
            logger.info(f"[ProjectEvaluator] evaluando {project['slug']}...")
            try:
                d1 = await _score_d1_usage(project)
                d2 = await _score_d2_technical(project, client)
                d3 = await _score_d3_model(project, client)
                d4 = await _score_d4_market(project, client)
                d5 = await _score_d5_promotion(project)
                d6 = await _score_d6_availability(project, client)
                d7 = await _score_d7_growth(project, client)

                d3_weighted = d3 if d3 is not None else None
                d3_for_calc = d3 if d3 is not None else 0

                total = (
                    d1 * WEIGHTS["d1"] +
                    d2 * WEIGHTS["d2"] +
                    d3_for_calc * (WEIGHTS["d3"] if d3 is not None else 0) +
                    d4 * WEIGHTS["d4"] +
                    d5 * WEIGHTS["d5"] +
                    d6 * WEIGHTS["d6"] +
                    d7 * WEIGHTS["d7"]
                )
                # Normalize if no model
                if d3 is None:
                    used_weights = sum(v for k, v in WEIGHTS.items() if k != "d3")
                    total = total / used_weights * 100 if used_weights else total

                total = round(total, 1)

                # Get LLM recommendations
                rec_response = await llm.ainvoke(RECOMMENDATIONS_PROMPT.format(
                    name=project["name"], d1=round(d1), d2=round(d2),
                    d3=f"{round(d3)}" if d3 is not None else "N/A",
                    d4=round(d4), d5=round(d5), d6=round(d6), d7=round(d7), total=total
                ))
                recommendations = rec_response.content.strip()

                score_data = {
                    "project": project["slug"],
                    "name": project["name"],
                    "total": total,
                    "month": month,
                    "dimensions": {
                        "d1": round(d1), "d2": round(d2), "d3": round(d3) if d3 is not None else None,
                        "d4": round(d4), "d5": round(d5), "d6": round(d6), "d7": round(d7)
                    },
                    "recommendations": recommendations,
                }
                scores.append(score_data)

                # Store in memory so other agents can read it
                memory.upsert(
                    collection="project_scores",
                    id=f"{project['slug']}_{month}",
                    document=recommendations,
                    metadata=score_data,
                )

            except Exception as e:
                logger.error(f"[ProjectEvaluator] {project['slug']}: {e}")

    # Sort by total score
    scores.sort(key=lambda x: x["total"], reverse=True)

    # Format Telegram report
    lines = [f"📊 <b>RANKING MENSUAL — {month}</b>\n"]
    for i, s in enumerate(scores, 1):
        d = s["dimensions"]
        d3_str = str(d.get("d3")) if d.get("d3") is not None else "—"
        trend = "📈" if s["total"] >= 70 else "📉" if s["total"] < 40 else "➡️"
        lines.append(
            f"{i}. {trend} <b>{s['name']}</b> — <b>{s['total']}/100</b>\n"
            f"   Uso:{d['d1']} Téc:{d['d2']} ML:{d3_str} Mkt:{d['d4']} Promo:{d['d5']}\n"
            f"   ▶ {s['recommendations'][:80]}"
        )

    report = "\n\n".join(lines)
    # Split if too long for Telegram
    for chunk in [report[i:i+3500] for i in range(0, len(report), 3500)]:
        await telegram_bot.send_alert(chunk)

    memory.log_event("project_evaluator", "report_generated", {"projects_evaluated": len(scores), "month": month})
    logger.info(f"[ProjectEvaluator] evaluados {len(scores)} proyectos")


if __name__ == "__main__":
    asyncio.run(evaluate_all_projects())
