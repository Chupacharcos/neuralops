"""
RecommendationRouter — el cerebro de la interconexión de agentes.

FASE A — Clasificar y enquelar (lunes 07:15, tras ProjectEvaluator):
  1. Lee recommendations de project_scores en memoria
  2. Usa LLM para clasificar cada recomendación en action_type
  3. AUTO actions → ejecuta directamente (promote, seo, github issue)
  4. CONFIRM actions → envía Telegram con botones [✅ Aprobar] [❌ Rechazar]

FASE B — Ejecutar aprobadas (cada 2h, vía cron */2):
  1. Lee pending_actions con status=approved
  2. Ejecuta según action_type
  3. Marca como done + notifica Telegram

Ejecución:
  python neuralops_cron.py recommendation_router          # FASE A + B
  python neuralops_cron.py recommendation_router --only-execute  # solo FASE B
"""
from __future__ import annotations
import os
import sys
import json
import asyncio
import logging
import httpx
from datetime import datetime, timezone
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from core.shared_context import load_system_context
from core.confirmation_queue import (
    async_queue_action, get_pending_auto_actions, get_approved_actions,
    mark_executed, approve_action,
)
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.1-8b-instant", api_key=os.getenv("GROQ_API_KEY"), temperature=0)

GH_USER    = os.getenv("GITHUB_USERNAME", "Chupacharcos")
GH_TOKEN   = os.getenv("GITHUB_TOKEN", "")
GH_HEADERS = {"Authorization": f"token {GH_TOKEN}", "Accept": "application/vnd.github.v3+json"}

CLASSIFY_PROMPT = """Analiza esta recomendación de mejora para un proyecto IA/ML de portfolio y clasifícala.

Proyecto: {name}
Score actual: {score}/100
Recomendación: {recommendation}
Contexto del sistema (semana): demos_caídas={demo_alerts}, emails_enviados={emails_sent}

Clasifica la recomendación en UNA de estas categorías:

AUTO (se ejecutan automáticamente sin aprobación):
- promote_project: mejorar visibilidad, publicar, promocionar, dar más exposición al proyecto
- seo_audit: mejorar SEO, palabras clave, posicionamiento, meta tags
- github_issue: crear issue técnico, trackear bug, añadir tarea de mejora en GitHub

CONFIRM (requieren aprobación humana por ser cambios grandes):
- model_retrain: reentrenar modelo, mejorar métricas ML, cambiar arquitectura del modelo
- infra_change: cambiar configuración de servicio, puerto, workers, nginx, systemd
- major_refactor: refactorizar código significativamente, rediseñar arquitectura del proyecto
- dependency_upgrade: actualizar dependencias, migrar versiones, cambiar librerías

Responde SOLO con JSON:
{{"action_type": "<tipo>", "priority": "<high|normal|low>", "detail": "<descripción concisa de qué hacer exactamente, máx 150 chars>"}}"""


# ── FASE A: Clasificar nuevas recomendaciones ─────────────────────────────────

async def _classify_recommendations():
    scores = memory.query("project_scores", n_results=50)
    ctx = load_system_context()

    if not scores:
        logger.info("[RecRouter] Sin scores en memoria — nada que clasificar")
        return 0

    current_month = datetime.now().strftime("%Y-%m")
    processed = 0
    actions_created = 0

    # Qué slugs ya tienen acciones este mes (evitar duplicados)
    existing = memory.query("pending_actions", n_results=200)
    already_routed = {
        r["metadata"].get("project")
        for r in existing
        if r["metadata"].get("month") == current_month
    }

    for score_record in scores:
        meta = score_record["metadata"]
        slug = meta.get("project")
        month = meta.get("month", "")

        if not slug or month != current_month:
            continue
        if slug in already_routed:
            logger.debug(f"[RecRouter] {slug} ya procesado este mes — saltando")
            continue

        recommendations = meta.get("recommendations", "").strip()
        if not recommendations:
            continue

        processed += 1
        name  = meta.get("name", slug)
        score = meta.get("total", 0)

        try:
            resp = await llm.ainvoke(CLASSIFY_PROMPT.format(
                name=name,
                score=score,
                recommendation=recommendations,
                demo_alerts=ctx["weekly"].get("demo_alerts", 0),
                emails_sent=ctx["weekly"].get("emails_sent", 0),
            ))
            raw = resp.content.strip()
            start, end = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[start:end])

            action_type = data.get("action_type", "github_issue")
            priority    = data.get("priority", "normal")
            detail      = data.get("detail", recommendations[:150])

            # Ajustar prioridad según score bajo
            if score < 40 and priority == "normal":
                priority = "high"

            action_id = await async_queue_action(
                action_type=action_type,
                project=slug,
                payload={"slug": slug, "name": name, "detail": detail, "score": score, "month": month},
                message=f"<b>{name}</b> (score {score}/100)\n\n{detail}",
                priority=priority,
            )
            actions_created += 1
            logger.info(f"[RecRouter] {slug} → {action_type} ({priority}) [{action_id}]")

        except Exception as e:
            logger.error(f"[RecRouter] Error clasificando {slug}: {e}")

    return actions_created


# ── FASE B: Ejecutar acciones (auto + aprobadas) ──────────────────────────────

async def _execute_actions():
    executed = 0

    # AUTO actions (no requieren confirmación)
    auto_actions = get_pending_auto_actions()
    for action in auto_actions:
        meta = action["metadata"]
        action_id   = action["id"]
        action_type = meta.get("action_type")
        payload     = meta.get("payload", {})
        slug        = meta.get("project", "")

        try:
            result = await _dispatch(action_type, slug, payload)
            mark_executed(action_id, result)
            executed += 1
            await telegram_bot.send_alert(
                f"⚡ <b>Acción automática ejecutada</b>\n"
                f"Tipo: <code>{action_type}</code>\n"
                f"Proyecto: <code>{slug}</code>\n"
                f"Resultado: {result}"
            )
            logger.info(f"[RecRouter] AUTO ejecutada: {action_id} ({action_type}/{slug}) → {result}")
        except Exception as e:
            logger.error(f"[RecRouter] Error ejecutando {action_id}: {e}")

    # CONFIRM actions aprobadas por el usuario
    approved_actions = get_approved_actions()
    for action in approved_actions:
        meta = action["metadata"]
        action_id   = action["id"]
        action_type = meta.get("action_type")
        payload     = meta.get("payload", {})
        slug        = meta.get("project", "")

        try:
            result = await _dispatch(action_type, slug, payload)
            mark_executed(action_id, result)
            executed += 1
            await telegram_bot.send_alert(
                f"✅ <b>Acción ejecutada</b>\n"
                f"Tipo: <code>{action_type}</code>\n"
                f"Proyecto: <code>{slug}</code>\n"
                f"Resultado: {result}"
            )
            logger.info(f"[RecRouter] CONFIRM ejecutada: {action_id} ({action_type}/{slug}) → {result}")
        except Exception as e:
            logger.error(f"[RecRouter] Error ejecutando aprobada {action_id}: {e}")
            await telegram_bot.send_alert(
                f"❌ <b>Error ejecutando acción aprobada</b>\n"
                f"ID: <code>{action_id}</code>\nError: {e}"
            )

    return executed


async def _dispatch(action_type: str, slug: str, payload: dict) -> str:
    """Despacha la acción al executor correcto."""
    if action_type == "promote_project":
        return await _exec_promote(slug, payload)
    elif action_type == "seo_audit":
        return await _exec_seo_audit(slug)
    elif action_type == "github_issue":
        return await _exec_github_issue(slug, payload)
    elif action_type == "model_retrain":
        return await _exec_model_retrain_guide(slug, payload)
    elif action_type == "infra_change":
        return await _exec_infra_guide(slug, payload)
    elif action_type in ("major_refactor", "dependency_upgrade"):
        return await _exec_code_task(action_type, slug, payload)
    else:
        return f"action_type desconocido: {action_type}"


# ── Executors ─────────────────────────────────────────────────────────────────

async def _exec_promote(slug: str, payload: dict) -> str:
    """Sube el proyecto a la cola de prioridad de promoción."""
    name  = payload.get("name", slug)
    score = payload.get("score", 0)
    month = payload.get("month", datetime.now().strftime("%Y-%m"))

    memory.upsert(
        collection="promotion_priority",
        id=f"promo_{slug}",
        document=f"Promocionar {name} — recomendado por RecommendationRouter (score {score})",
        metadata={"slug": slug, "name": name, "score": score, "status": "active",
                  "month": month, "added_at": datetime.now().isoformat()},
    )
    memory.log_event("recommendation_router", "promote_queued", {"slug": slug, "score": score})
    return f"Proyecto '{name}' añadido a promotion_priority — será el siguiente en LinkedIn/Twitter"


async def _exec_seo_audit(slug: str) -> str:
    """Marca el slug para que seo_monitor lo priorice en su próxima ejecución."""
    memory.upsert(
        collection="seo_priority",
        id=f"seo_{slug}",
        document=f"Auditoría SEO solicitada para {slug}",
        metadata={"slug": slug, "status": "requested", "requested_at": datetime.now().isoformat()},
    )
    memory.log_event("recommendation_router", "seo_requested", {"slug": slug})
    return f"Auditoría SEO encolada para '{slug}' — seo_monitor lo procesará el próximo miércoles"


async def _exec_github_issue(slug: str, payload: dict) -> str:
    """Crea un GitHub issue con la recomendación de mejora."""
    if not GH_TOKEN:
        return "GitHub token no configurado — issue no creado"

    name   = payload.get("name", slug)
    detail = payload.get("detail", "Mejora recomendada por ProjectEvaluator")
    repo   = _slug_to_repo(slug)

    if not repo:
        return f"Repo no mapeado para '{slug}' — issue no creado (añadir a _slug_to_repo)"

    body = (
        f"## Mejora recomendada por ProjectEvaluator\n\n"
        f"**Score actual:** {payload.get('score', '?')}/100 · **Mes:** {payload.get('month', '?')}\n\n"
        f"### Acción recomendada\n{detail}\n\n"
        f"---\n*Generado automáticamente por NeuralOps RecommendationRouter*"
    )

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://api.github.com/repos/{GH_USER}/{repo}/issues",
                headers=GH_HEADERS,
                json={"title": f"[NeuralOps] Mejora: {detail[:80]}", "body": body,
                      "labels": ["improvement", "neuralops"]},
                timeout=15,
            )
        if resp.status_code == 201:
            url = resp.json().get("html_url", "")
            memory.log_event("recommendation_router", "github_issue_created",
                             {"slug": slug, "repo": repo, "url": url})
            return f"Issue creado: {url}"
        else:
            return f"GitHub API error {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return f"Error creando issue: {e}"


async def _exec_model_retrain_guide(slug: str, payload: dict) -> str:
    """Genera una guía de reentrenamiento y la envía por Telegram."""
    detail = payload.get("detail", "Mejorar rendimiento del modelo")
    await telegram_bot.send_alert(
        f"🔁 <b>Guía de reentrenamiento — {payload.get('name', slug)}</b>\n\n"
        f"<b>Acción:</b> {detail}\n\n"
        f"Para reentrenar:\n"
        f"<code>cd /var/www/{slug}\n"
        f"source /var/www/chatbot/venv/bin/activate\n"
        f"python train.py\n"
        f"sudo systemctl restart {_slug_to_service(slug)}</code>"
    )
    memory.log_event("recommendation_router", "retrain_guide_sent", {"slug": slug})
    return "Guía de reentrenamiento enviada por Telegram"


async def _exec_infra_guide(slug: str, payload: dict) -> str:
    """Envía guía de cambio de infraestructura."""
    detail = payload.get("detail", "Cambio de configuración recomendado")
    await telegram_bot.send_alert(
        f"⚙️ <b>Cambio de infraestructura — {payload.get('name', slug)}</b>\n\n"
        f"<b>Acción:</b> {detail}\n\n"
        f"Servicio: <code>{_slug_to_service(slug)}</code>\n"
        f"Config: <code>/etc/systemd/system/{_slug_to_service(slug)}.service</code>"
    )
    memory.log_event("recommendation_router", "infra_guide_sent", {"slug": slug})
    return "Guía de infra enviada por Telegram"


async def _exec_code_task(action_type: str, slug: str, payload: dict) -> str:
    """Para dependency_upgrade aprobado: ejecuta pip install. Para major_refactor: crea issue."""
    if action_type == "dependency_upgrade":
        return await _exec_dependency_upgrade(slug, payload)
    return await _exec_github_issue(slug, payload)


async def _exec_dependency_upgrade(slug: str, payload: dict) -> str:
    """Ejecuta pip install de las dependencias aprobadas en el venv del proyecto."""
    import subprocess
    packages = payload.get("packages", [])
    if not packages:
        packages = [payload.get("detail", "").split()[0]] if payload.get("detail") else []
    if not packages:
        return "Sin paquetes especificados para actualizar"

    # Determinar venv del proyecto
    project_dir = f"/var/www/{slug.replace('-ai','').replace('-engine','')}"
    venv_python = f"{project_dir}/venv/bin/pip"
    if not __import__('pathlib').Path(venv_python).exists():
        venv_python = "/var/www/chatbot/venv/bin/pip"

    results = []
    for pkg in packages[:5]:
        try:
            r = subprocess.run(
                [venv_python, "install", "--upgrade", pkg],
                capture_output=True, text=True, timeout=120
            )
            if r.returncode == 0:
                results.append(f"✓ {pkg}")
            else:
                results.append(f"✗ {pkg}: {r.stderr[:80]}")
        except Exception as e:
            results.append(f"✗ {pkg}: {e}")

    result_str = " | ".join(results)
    memory.log_event("recommendation_router", "deps_upgraded", {"slug": slug, "results": result_str})
    return f"Dependencias actualizadas en {slug}: {result_str}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug_to_repo(slug: str) -> str | None:
    mapping = {
        "chatbot-manual":                   "cv-web",
        "prediccion-precio-inmobiliario":   "proyecto-inmobiliario",
        "deteccion-zonas-revalorizacion":   "proyecto-revalorizacion",
        "prediccion-calidad-aire":          "calidad-aire",
        "babymind":                         "BabyMind",
        "metacoach":                        "MetaCoach",
        "stem-splitter":                    "Stem-Splitter",
        "feliniai":                         "FeliniAI",
        "sports-engine":                    "Sports-Performance-Engine",
        "fraud-detector":                   "Fraud-Detection-Pipeline",
        "value-betting":                    "Value-Betting-Engine",
        "alphasignal":                      "AlphaSignal-Bot-de-Inversi-n-IA",
        "roomcraft-ai":                     "RoomCraft-AI",
        "adaptive-music-engine":            "Adaptive-Music-Engine",
    }
    return mapping.get(slug)


def _slug_to_service(slug: str) -> str:
    mapping = {
        "prediccion-precio-inmobiliario": "ml-inmobiliario",
        "deteccion-zonas-revalorizacion": "ml-revalorizacion",
        "prediccion-calidad-aire":        "ml-calidad-aire",
        "babymind":                       "babymind",
        "metacoach":                      "metacoach",
        "stem-splitter":                  "stem-splitter",
        "feliniai":                       "feliniai",
        "sports-engine":                  "sports-engine",
        "fraud-detector":                 "fraud-detector",
        "value-betting":                  "value-engine",
        "alphasignal":                    "alphasignal",
        "roomcraft-ai":                   "roomcraft",
    }
    return mapping.get(slug, slug)


# ── Entry point ───────────────────────────────────────────────────────────────

async def recommendation_router():
    only_execute = "--only-execute" in sys.argv

    executed = await _execute_actions()
    logger.info(f"[RecRouter] FASE B: {executed} acciones ejecutadas")

    if only_execute:
        if executed:
            await telegram_bot.send_alert(
                f"⚡ <b>RecommendationRouter</b> — {executed} acciones ejecutadas"
            )
        return

    # FASE A: clasificar nuevas recomendaciones
    actions_created = await _classify_recommendations()
    logger.info(f"[RecRouter] FASE A: {actions_created} acciones creadas")

    if actions_created or executed:
        await telegram_bot.send_alert(
            f"🧠 <b>RecommendationRouter</b>\n"
            f"Nuevas acciones encoladas: <b>{actions_created}</b>\n"
            f"Acciones ejecutadas: <b>{executed}</b>\n\n"
            f"<i>Las acciones AUTO se ejecutarán automáticamente.\n"
            f"Las acciones CONFIRM requieren tu aprobación (botones enviados).</i>"
        )
    else:
        logger.info("[RecRouter] Sin cambios — todo al día")


if __name__ == "__main__":
    asyncio.run(recommendation_router())
