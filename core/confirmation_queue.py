"""
Cola de acciones con aprobación humana via Telegram.

Flujo:
  1. queue_action(...)       → guarda en memory["pending_actions"] + envía botones Telegram
  2. response_handler        → detecta botón pulsado → llama approve/reject
  3. get_approved_actions()  → recommendation_router los ejecuta
  4. mark_executed(...)      → cierra el ciclo con resultado

Tipos de acción (action_type):
  AUTO (se ejecutan sin confirmación):
    promote_project     → sube prioridad de promoción
    seo_audit           → dispara seo_monitor para ese proyecto
    github_issue        → crea issue en GitHub

  CONFIRM (requieren Telegram):
    model_retrain       → reentrenar modelo ML
    infra_change        → cambio en servicio/puerto/config
    major_refactor      → refactoring gordo de código
    dependency_upgrade  → actualizar dependencias críticas
"""
from __future__ import annotations
import uuid
import logging
from datetime import datetime
from core import memory, telegram_bot

logger = logging.getLogger(__name__)

CONFIRM_TYPES = {"model_retrain", "infra_change", "major_refactor", "dependency_upgrade"}
AUTO_TYPES    = {"promote_project", "seo_audit", "github_issue"}


def queue_action(
    action_type: str,
    project: str,
    payload: dict,
    message: str,
    priority: str = "normal",  # "high" | "normal" | "low"
) -> str:
    """
    Encola una acción. Si es AUTO, la registra directamente como pending para
    que recommendation_router la ejecute en el mismo ciclo. Si es CONFIRM,
    envía mensaje Telegram con botones y la deja en pending.
    Devuelve el action_id.
    """
    action_id = f"act_{uuid.uuid4().hex[:10]}"

    memory.upsert(
        collection="pending_actions",
        id=action_id,
        document=message,
        metadata={
            "action_id":   action_id,
            "action_type": action_type,
            "project":     project,
            "payload":     payload,
            "priority":    priority,
            "status":      "pending",
            "requires_confirm": action_type in CONFIRM_TYPES,
            "created_at":  datetime.now().isoformat(),
        },
    )

    if action_type in CONFIRM_TYPES:
        import asyncio
        asyncio.get_event_loop().run_until_complete(_send_confirmation(action_id, action_type, project, message))
        logger.info(f"[ConfirmQueue] acción CONFIRM enviada a Telegram: {action_id} ({action_type}/{project})")
    else:
        logger.info(f"[ConfirmQueue] acción AUTO encolada: {action_id} ({action_type}/{project})")

    return action_id


async def async_queue_action(
    action_type: str,
    project: str,
    payload: dict,
    message: str,
    priority: str = "normal",
) -> str:
    """Versión async de queue_action para usar dentro de coroutines."""
    action_id = f"act_{uuid.uuid4().hex[:10]}"

    memory.upsert(
        collection="pending_actions",
        id=action_id,
        document=message,
        metadata={
            "action_id":   action_id,
            "action_type": action_type,
            "project":     project,
            "payload":     payload,
            "priority":    priority,
            "status":      "pending",
            "requires_confirm": action_type in CONFIRM_TYPES,
            "created_at":  datetime.now().isoformat(),
        },
    )

    if action_type in CONFIRM_TYPES:
        await _send_confirmation(action_id, action_type, project, message)
        logger.info(f"[ConfirmQueue] CONFIRM enviada: {action_id} ({action_type}/{project})")
    else:
        logger.info(f"[ConfirmQueue] AUTO encolada: {action_id} ({action_type}/{project})")

    return action_id


async def _send_confirmation(action_id: str, action_type: str, project: str, message: str):
    label_map = {
        "model_retrain":      "🔁 Reentrenamiento de modelo",
        "infra_change":       "⚙️ Cambio de infraestructura",
        "major_refactor":     "🛠 Refactoring mayor",
        "dependency_upgrade": "📦 Actualización de dependencias",
    }
    label = label_map.get(action_type, action_type)
    await telegram_bot.send_alert(
        f"🔔 <b>Acción pendiente de aprobación</b>\n\n"
        f"<b>Tipo:</b> {label}\n"
        f"<b>Proyecto:</b> <code>{project}</code>\n\n"
        f"{message}\n\n"
        f"<i>ID: <code>{action_id}</code></i>",
        buttons=[[
            {"text": "✅ Aprobar", "data": f"approve_action:{action_id}"},
            {"text": "❌ Rechazar", "data": f"reject_action:{action_id}"},
        ]],
    )


def approve_action(action_id: str):
    _set_status(action_id, "approved")


def reject_action(action_id: str):
    _set_status(action_id, "rejected")


def mark_executed(action_id: str, result: str = "ok"):
    results = memory.query("pending_actions", n_results=200)
    for r in results:
        if r["id"] == action_id:
            meta = {**r["metadata"], "status": "done", "result": result,
                    "executed_at": datetime.now().isoformat()}
            memory.upsert("pending_actions", action_id, r["document"], meta)
            return


def get_pending_auto_actions() -> list[dict]:
    """Retorna acciones AUTO pendientes (no requieren confirmación)."""
    results = memory.query("pending_actions", n_results=100)
    return [
        r for r in results
        if r["metadata"].get("status") == "pending"
        and not r["metadata"].get("requires_confirm", False)
    ]


def get_approved_actions() -> list[dict]:
    """Retorna acciones CONFIRM ya aprobadas por el usuario."""
    results = memory.query("pending_actions", n_results=100)
    return [r for r in results if r["metadata"].get("status") == "approved"]


def _set_status(action_id: str, status: str):
    results = memory.query("pending_actions", n_results=200)
    for r in results:
        if r["id"] == action_id:
            meta = {**r["metadata"], "status": status, "updated_at": datetime.now().isoformat()}
            memory.upsert("pending_actions", action_id, r["document"], meta)
            logger.info(f"[ConfirmQueue] {action_id} → {status}")
            return
    logger.warning(f"[ConfirmQueue] action_id no encontrado: {action_id}")
