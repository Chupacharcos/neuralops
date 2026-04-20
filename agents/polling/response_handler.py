"""Gestiona callbacks de Telegram (botones inline) y aprobaciones pendientes."""
import logging
import json
from graph.state import NeuralOpsState
from core import telegram_bot, memory

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

            if data.startswith("approve:"):
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

    except Exception as e:
        logger.error(f"[ResponseHandler] Error al leer updates: {e}")

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
