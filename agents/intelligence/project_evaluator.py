"""ProjectEvaluator — 9 dimensiones. 1er lunes del mes."""
import os, asyncio, logging, json, re, subprocess, sqlite3, time
from datetime import datetime
from pathlib import Path
import httpx
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0)

PROJECTS_PATH = "/var/www/neuralops/projects.json"
LEADS_DB      = "/var/www/neuralops/leads.db"
NGINX_LOG     = "/var/log/nginx/access.log"
GH_USER       = os.getenv("GITHUB_USERNAME")
GH_TOKEN      = os.getenv("GITHUB_TOKEN")
GH_HEADERS    = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}

# Pesos de las 9 dimensiones (suman 1.0)
W = {
    "d1": 0.15,   # Tráfico de demo (visitas + llamadas API)
    "d2": 0.12,   # Salud técnica (issues GitHub + latencia)
    "d3": 0.10,   # Calidad del modelo ML (drift)
    "d4": 0.15,   # Impacto en leads (scores, emails, aperturas)
    "d5": 0.13,   # Engagement en demo (interacciones profundas)
    "d6": 0.10,   # Disponibilidad (fallos DemoWatcher)
    "d7": 0.10,   # Calidad de código (complejidad radon + bugs GitHub)
    "d8": 0.10,   # Comparación de mercado (SOTA + competidores)
    "d9": 0.05,   # Crecimiento (GitHub stars/forks)
}


def _load_projects():
    with open(PROJECTS_PATH) as f:
        return json.load(f)


def _nginx_lines():
    try:
        r = subprocess.run(["tail", "-n", "20000", NGINX_LOG], capture_output=True, text=True, timeout=10)
        return r.stdout.splitlines()
    except Exception:
        return []


# ── D1: Tráfico demo (visitas página + llamadas API) ─────────────────────────

async def _score_d1_traffic(project: dict, nginx_lines: list) -> float:
    slug = project["slug"]
    page_views = sum(1 for l in nginx_lines if f"/demo/{slug}" in l and "GET" in l)
    api_calls   = sum(1 for l in nginx_lines if f"/api/{slug}/" in l or f"/demo/{slug}/" in l and "POST" in l)
    total = page_views + api_calls * 3  # API calls pesan más (indican uso real)
    return min(100, total / 8)          # 800 interacciones = 100 puntos


# ── D2: Salud técnica (GitHub issues + latencia) ─────────────────────────────

async def _score_d2_technical(project: dict, client: httpx.AsyncClient) -> float:
    score = 85.0
    repo = project.get("github_repo")

    if repo:
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{GH_USER}/{repo}/issues",
                headers=GH_HEADERS, params={"state": "open", "per_page": 50}, timeout=10
            )
            if resp.status_code == 200:
                issues = [i for i in resp.json() if "pull_request" not in i]
                bugs = [i for i in issues if any(l["name"] in ("bug", "error") for l in i.get("labels", []))]
                score -= min(30, len(issues) * 4)
                score -= min(20, len(bugs) * 8)   # bugs penalizan más que enhancements
        except Exception:
            pass

    health_url = project.get("health_url")
    if health_url:
        try:
            t0 = time.monotonic()
            resp = await client.get(health_url, timeout=5)
            latency_ms = (time.monotonic() - t0) * 1000
            if resp.status_code != 200:
                score -= 25
            elif latency_ms > 3000:
                score -= 15
            elif latency_ms > 1500:
                score -= 7
        except Exception:
            score -= 30

    return max(0, score)


# ── D3: Calidad del modelo ML ─────────────────────────────────────────────────

async def _score_d3_model(project: dict) -> float | None:
    if not project.get("has_model"):
        return None
    baselines = {
        "prediccion-precio-inmobiliario": 0.88, "prediccion-calidad-aire": 0.416,
        "fraud-detector": 0.9999, "sports-engine": 0.72, "feliniai": 0.9675,
        "alphasignal": 0.65, "value-betting": 0.70,
    }
    events = memory.query("model_drift", where={"slug": project["slug"]}, n_results=1)
    if events:
        score_val = events[0]["metadata"].get("score")
        baseline  = baselines.get(project["slug"])
        if score_val and baseline:
            return min(100, (score_val / baseline) * 80)
    return 60.0


# ── D4: Impacto en leads (leads.db) ──────────────────────────────────────────

async def _score_d4_leads(project: dict) -> float:
    if not Path(LEADS_DB).exists():
        return 30.0
    slug = project["slug"]
    score = 0.0
    try:
        conn = sqlite3.connect(LEADS_DB)

        # Leads generados para este proyecto y su score promedio
        rows = conn.execute(
            "SELECT score FROM leads WHERE project_slug=? AND score IS NOT NULL", (slug,)
        ).fetchall()
        if rows:
            avg_score = sum(r[0] for r in rows) / len(rows)
            score += min(35, len(rows) * 5)        # hasta 35 pts por volumen
            score += min(25, avg_score * 0.25)     # hasta 25 pts por calidad media

        # Emails enviados y aperturas (tracking)
        emails = conn.execute(
            "SELECT e.tracking_id FROM emails_sent e WHERE e.project_slug=?", (slug,)
        ).fetchall()
        n_sent = len(emails)
        score += min(20, n_sent * 4)

        if n_sent > 0:
            tracking_ids = [e[0] for e in emails if e[0]]
            if tracking_ids:
                placeholders = ",".join("?" * len(tracking_ids))
                opens = conn.execute(
                    f"SELECT COUNT(*) FROM tracking WHERE tracking_id IN ({placeholders}) AND event='open'",
                    tracking_ids
                ).fetchone()[0]
                open_rate = opens / n_sent
                score += min(20, open_rate * 60)   # 33% open rate = 20 pts

        conn.close()
    except Exception as e:
        logger.error(f"[Evaluator] leads D4 {slug}: {e}")

    return min(100, score)


# ── D5: Engagement profundo en demo ──────────────────────────────────────────

async def _score_d5_engagement(project: dict, nginx_lines: list) -> float:
    slug = project["slug"]
    # Llamadas POST a endpoints del proyecto = uso activo de la demo
    api_hits = [l for l in nginx_lines if (f"/api/{slug}" in l or f"/demo/{slug}/" in l) and ("POST" in l or "200" in l)]
    # IPs únicas que hicieron más de 2 llamadas (usuario que exploró, no rebote)
    from collections import Counter
    ip_pattern = re.compile(r"^(\d+\.\d+\.\d+\.\d+)")
    ips = [m.group(1) for l in api_hits if (m := ip_pattern.match(l))]
    engaged = sum(1 for _, n in Counter(ips).items() if n >= 2)
    score = min(60, len(api_hits) / 5) + min(40, engaged * 8)
    return min(100, score)


# ── D6: Disponibilidad (fallos DemoWatcher) ───────────────────────────────────

async def _score_d6_availability(project: dict) -> float:
    score = 100.0
    events = memory.query("events", where={"agent": "demo_watcher"}, n_results=100)
    failures = [e for e in events if project["slug"] in (e.get("document") or "")]
    score -= min(60, len(failures) * 10)
    return max(0, score)


# ── D7: Calidad de código (radon + bugs GitHub) ───────────────────────────────

async def _score_d7_code_quality(project: dict, client: httpx.AsyncClient) -> float:
    score = 70.0
    slug  = project["slug"]
    project_path = Path(f"/var/www/{slug}")

    # Complejidad ciclomática con radon
    if project_path.is_dir():
        try:
            py_files = list(project_path.glob("*.py")) + list(project_path.glob("**/*.py"))
            py_files = [f for f in py_files if "venv" not in str(f) and "__pycache__" not in str(f)][:10]
            if py_files:
                r = subprocess.run(
                    ["/var/www/neuralops/venv/bin/radon", "cc", "--average", "-s"] + [str(f) for f in py_files],
                    capture_output=True, text=True, timeout=15
                )
                # radon average: A=bajo, B=ok, C=alto, D/E/F=muy alto
                for grade, penalty in [("F", 35), ("E", 25), ("D", 15), ("C", 8)]:
                    if f"Average complexity: {grade}" in r.stdout:
                        score -= penalty
                        break
        except Exception:
            pass

    # Bugs en GitHub (ya contados en D2, aquí sólo penalizamos duplicados = issues sin respuesta)
    repo = project.get("github_repo")
    if repo:
        try:
            resp = await client.get(
                f"https://api.github.com/repos/{GH_USER}/{repo}/issues",
                headers=GH_HEADERS, params={"state": "open", "per_page": 50}, timeout=10
            )
            if resp.status_code == 200:
                stale = [i for i in resp.json()
                         if "pull_request" not in i and len(i.get("comments", [])) == 0]
                score -= min(20, len(stale) * 5)  # issues sin ninguna respuesta = stale
        except Exception:
            pass

    # Tests presentes = bonus
    if (project_path / "tests").is_dir() or list(project_path.glob("test_*.py")):
        score += 10

    return max(0, min(100, score))


# ── D8: Comparación de mercado (LLM vs SOTA) ─────────────────────────────────

MARKET_PROMPT = """Evalúa este proyecto de portfolio IA en el contexto del mercado actual (2025).

Proyecto: {name}
Descripción: {desc}
Tecnologías: {tech}
Sector: {sector}
Menciones de competidores detectadas: {competitors}

Puntúa de 0 a 100 considerando:
- Diferenciación real vs alternativas públicas (open source o comerciales)
- Relevancia del problema que resuelve en 2025
- Nivel técnico respecto al estado del arte
- Originalidad del enfoque

Responde SOLO con un número entero de 0 a 100."""

async def _score_d8_market(project: dict, client: httpx.AsyncClient) -> float:
    competitors = memory.query("competitor_mentions", where={"sector": project.get("sector", "")}, n_results=5)
    comp_text = ", ".join(e.get("document", "") for e in competitors) or "ninguno detectado"

    try:
        resp = await llm.ainvoke(MARKET_PROMPT.format(
            name=project.get("name", project["slug"]),
            desc=project.get("description", "Proyecto IA/ML"),
            tech=", ".join(project.get("keywords", [])),
            sector=project.get("sector", "General"),
            competitors=comp_text[:300],
        ))
        score = int(re.search(r"\d+", resp.content).group())
        return min(100, max(0, score))
    except Exception:
        return 60.0


# ── D9: Crecimiento (GitHub stars/forks) ─────────────────────────────────────

async def _score_d9_growth(project: dict, client: httpx.AsyncClient) -> float:
    repo = project.get("github_repo")
    if not repo:
        return 30.0
    try:
        resp = await client.get(f"https://api.github.com/repos/{GH_USER}/{repo}",
                                headers=GH_HEADERS, timeout=10)
        if resp.status_code == 200:
            d = resp.json()
            return min(100, d.get("stargazers_count", 0) * 8 + d.get("forks_count", 0) * 15)
    except Exception:
        pass
    return 25.0


# ── Prompt de recomendaciones ─────────────────────────────────────────────────

REC_PROMPT = """Proyecto de portfolio IA con estos scores (0-100):

{name}
D1 Tráfico demo:       {d1}/100
D2 Salud técnica:      {d2}/100
D3 Modelo ML:          {d3}
D4 Impacto leads:      {d4}/100
D5 Engagement demo:    {d5}/100
D6 Disponibilidad:     {d6}/100
D7 Calidad código:     {d7}/100
D8 Posición mercado:   {d8}/100
D9 Crecimiento GitHub: {d9}/100
Score total:           {total}/100

Da 2 acciones concretas y prioritarias para mejorar el score. Máx 1 línea cada una."""


# ── Main ─────────────────────────────────────────────────────────────────────

async def evaluate_all_projects():
    projects    = _load_projects()
    nginx_lines = _nginx_lines()
    month       = datetime.now().strftime("%Y-%m")
    scores      = []

    async with httpx.AsyncClient() as client:
        for project in projects:
            logger.info(f"[Evaluator] {project['slug']}…")
            try:
                d1 = await _score_d1_traffic(project, nginx_lines)
                d2 = await _score_d2_technical(project, client)
                d3 = await _score_d3_model(project)
                d4 = await _score_d4_leads(project)
                d5 = await _score_d5_engagement(project, nginx_lines)
                d6 = await _score_d6_availability(project)
                d7 = await _score_d7_code_quality(project, client)
                d8 = await _score_d8_market(project, client)
                d9 = await _score_d9_growth(project, client)

                d3_val = d3 if d3 is not None else 0.0
                w3     = W["d3"] if d3 is not None else 0.0
                w_rest = 1.0 - w3 + W["d3"]  # normalizar si no hay modelo

                total = (
                    d1 * W["d1"] + d2 * W["d2"] + d3_val * w3 +
                    d4 * W["d4"] + d5 * W["d5"] + d6 * W["d6"] +
                    d7 * W["d7"] + d8 * W["d8"] + d9 * W["d9"]
                )
                if d3 is None:
                    used = sum(v for k, v in W.items() if k != "d3")
                    total = total / used * 100

                total = round(total, 1)

                rec = await llm.ainvoke(REC_PROMPT.format(
                    name=project.get("name", project["slug"]),
                    d1=round(d1), d2=round(d2),
                    d3=f"{round(d3)}" if d3 is not None else "N/A",
                    d4=round(d4), d5=round(d5), d6=round(d6),
                    d7=round(d7), d8=round(d8), d9=round(d9),
                    total=total,
                ))

                score_data = {
                    "project": project["slug"],
                    "name":    project.get("name", project["slug"]),
                    "total":   total,
                    "month":   month,
                    "dimensions": {
                        "d1": round(d1), "d2": round(d2),
                        "d3": round(d3) if d3 is not None else None,
                        "d4": round(d4), "d5": round(d5), "d6": round(d6),
                        "d7": round(d7), "d8": round(d8), "d9": round(d9),
                    },
                    "recommendations": rec.content.strip(),
                }
                scores.append(score_data)

                memory.upsert(
                    collection="project_scores",
                    id=f"{project['slug']}_{month}",
                    document=rec.content.strip(),
                    metadata=score_data,
                )

            except Exception as e:
                logger.error(f"[Evaluator] {project['slug']}: {e}")

    scores.sort(key=lambda x: x["total"], reverse=True)

    dim_labels = {
        "d1": "Tráfico", "d2": "Técnica", "d3": "Modelo",
        "d4": "Leads",   "d5": "Engagement", "d6": "Uptime",
        "d7": "Código",  "d8": "Mercado", "d9": "GitHub",
    }

    lines = [f"📊 <b>RANKING MENSUAL — {month}</b>\n"]
    for i, s in enumerate(scores, 1):
        d = s["dimensions"]
        trend = "📈" if s["total"] >= 70 else "📉" if s["total"] < 40 else "➡️"
        dim_str = " ".join(
            f"{dim_labels[k]}:{v}" for k, v in d.items() if v is not None
        )
        lines.append(
            f"{i}. {trend} <b>{s['name']}</b> — <b>{s['total']}/100</b>\n"
            f"   {dim_str}\n"
            f"   ▶ {s['recommendations'][:90]}"
        )

    report = "\n\n".join(lines)
    for chunk in [report[i:i+3800] for i in range(0, len(report), 3800)]:
        await telegram_bot.send_alert(chunk)

    memory.log_event("project_evaluator", "report_generated", {
        "projects_evaluated": len(scores), "month": month
    })
    logger.info(f"[Evaluator] {len(scores)} proyectos evaluados")


if __name__ == "__main__":
    asyncio.run(evaluate_all_projects())
