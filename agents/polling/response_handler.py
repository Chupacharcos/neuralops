"""Gestiona callbacks de Telegram (botones inline) y aprobaciones pendientes."""
import logging
import json
from graph.state import NeuralOpsState
from core import telegram_bot, memory
from core.confirmation_queue import approve_action, reject_action
from core.agent_status import report

logger = logging.getLogger(__name__)


async def response_handler(state: NeuralOpsState) -> NeuralOpsState:
    bot = telegram_bot.get_bot()

    # ── Leer updates de Telegram (callback_query = botones inline) ──────────
    try:
        offset = state.get("telegram_offset", 0)
        updates = await bot.get_updates(offset=offset, timeout=5, allowed_updates=["callback_query"])

        for update in updates:
            offset = update.update_id + 1
            cq = update.callback_query
            if not cq:
                continue

            data = cq.data or ""
            await cq.answer()  # quita el spinner del botón

            if data.startswith("approve_action:"):
                # Acciones del RecommendationRouter
                action_id = data[len("approve_action:"):]
                approve_action(action_id)
                await telegram_bot.send_alert(
                    f"✅ Acción aprobada: <code>{action_id}</code>\n"
                    f"<i>Se ejecutará en el próximo ciclo del RecommendationRouter (≤2h)</i>"
                )
                logger.info(f"[ResponseHandler] Acción aprobada: {action_id}")

            elif data.startswith("reject_action:"):
                action_id = data[len("reject_action:"):]
                reject_action(action_id)
                await telegram_bot.send_alert(f"❌ Acción rechazada: <code>{action_id}</code>")
                logger.info(f"[ResponseHandler] Acción rechazada: {action_id}")

            elif data.startswith("approve_draft:"):
                # Borradores de email del EmailDrafter
                draft_id = data[len("approve_draft:"):]
                _handle_draft_approval(draft_id, approved=True)
                await telegram_bot.send_alert(
                    f"✅ Borrador aprobado: <code>{draft_id}</code>\n"
                    f"<i>El EmailSender lo enviará en el próximo ciclo (10:30)</i>"
                )
                logger.info(f"[ResponseHandler] Borrador aprobado: {draft_id}")

            elif data.startswith("reject_draft:"):
                draft_id = data[len("reject_draft:"):]
                _handle_draft_approval(draft_id, approved=False)
                await telegram_bot.send_alert(f"🗑 Borrador descartado: <code>{draft_id}</code>")
                logger.info(f"[ResponseHandler] Borrador descartado: {draft_id}")

            elif data.startswith("approve:"):
                # Compatibilidad: aprobaciones de PDF/pending_updates
                conf_id = data[len("approve:"):]
                _handle_approval(conf_id, approved=True)
                await telegram_bot.send_alert(f"✅ Aprobado: <code>{conf_id}</code>")
                logger.info(f"[ResponseHandler] Aprobado: {conf_id}")

            elif data.startswith("reject:"):
                conf_id = data[len("reject:"):]
                _handle_approval(conf_id, approved=False)
                await telegram_bot.send_alert(f"❌ Rechazado: <code>{conf_id}</code>")
                logger.info(f"[ResponseHandler] Rechazado: {conf_id}")

        state["telegram_offset"] = offset
        processed = offset - state.get("telegram_offset", offset)
        if processed > 0:
            report("response_handler", f"{processed} callbacks Telegram procesados", "ok")
        else:
            report("response_handler", "Escuchando Telegram — sin callbacks pendientes", "info")

    except Exception as e:
        logger.error(f"[ResponseHandler] Error al leer updates: {e}")
        report("response_handler", f"⚠ Error Telegram: {e}", "error")

    return state


def _handle_approval(conf_id: str, approved: bool):
    """Actualiza el estado de una confirmación pendiente en memoria."""
    # pending_updates: id → pdf_path, metadata con slug/status
    results = memory.query("pending_updates")
    for r in results:
        if r["id"] == conf_id:
            meta = json.loads(r.get("metadata") or "{}")
            meta["status"] = "approved" if approved else "rejected"
            memory.upsert("pending_updates", conf_id, r["document"], meta)

            if not approved:
                # Si rechazado: mover PDF a una carpeta de rechazados para revisión manual
                import shutil, pathlib
                pdf_path = pathlib.Path(meta.get("pdf", ""))
                if pdf_path.exists():
                    rejected_dir = pdf_path.parent / "rechazados"
                    rejected_dir.mkdir(exist_ok=True)
                    shutil.move(str(pdf_path), str(rejected_dir / pdf_path.name))
                    logger.info(f"[ResponseHandler] PDF movido a rechazados: {pdf_path.name}")
            return

    logger.warning(f"[ResponseHandler] conf_id no encontrado: {conf_id}")


def _handle_draft_approval(draft_id: str, approved: bool):
    """Aprueba o descarta un borrador de email del EmailDrafter."""
    results = memory.query("email_drafts", n_results=100)
    for r in results:
        if r["id"] == draft_id:
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            new_status = "approved" if approved else "rejected"
            memory.upsert("email_drafts", draft_id, r["document"], {**meta, "status": new_status})
            logger.info(f"[ResponseHandler] borrador {draft_id} → {new_status}")
            return
    logger.warning(f"[ResponseHandler] draft_id no encontrado: {draft_id}")
