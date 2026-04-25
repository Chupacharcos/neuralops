"""Gestiona callbacks (botones inline) Y mensajes texto (comandos /agente)."""
import os
import asyncio
import logging
import json
import time
import sqlite3
from pathlib import Path
from graph.state import NeuralOpsState
from core import telegram_bot, memory
from core.confirmation_queue import approve_action, reject_action
from core.agent_status import report
from core.agent_chat import route_command, list_all_commands, INTENT_REGISTRY

logger = logging.getLogger(__name__)

_OFFSET_FILE = Path("/var/www/neuralops/logs/telegram_offset.json")
AUTHORIZED_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))


def _load_offset() -> int:
    try:
        return json.loads(_OFFSET_FILE.read_text()).get("offset", 0)
    except Exception:
        return 0


def _save_offset(offset: int):
    _OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OFFSET_FILE.write_text(json.dumps({"offset": offset}))


# ── Comandos GLOBALES (no específicos de un agente) ──────────────────────────
async def _cmd_status() -> str:
    try:
        d = json.loads(Path("/var/www/neuralops/agent_status.json").read_text())
    except Exception:
        return "⚠ agent_status.json no disponible."
    if not d:
        return "Sin agentes reportando."
    now = time.time()
    lines = [f"<b>📊 Status — {len(d)} agentes</b>"]
    for k in sorted(d.keys()):
        e = d[k]
        age_min = (now - e.get("epoch", 0)) / 60
        lvl = e.get("level", "info")
        icon = {"ok": "✅", "warning": "⚠️", "error": "❌", "info": "ℹ️"}.get(lvl, "•")
        msg = e.get("msg", "?")[:45]
        lines.append(f"{icon} <code>{k:18s}</code> {msg} <i>({age_min:.0f}m)</i>")
    return "\n".join(lines)


async def _cmd_leads() -> str:
    c = sqlite3.connect("/var/www/neuralops/leads.db").cursor()
    c.execute("SELECT name, company, email, score, status FROM leads ORDER BY score DESC LIMIT 10")
    rows = c.fetchall()
    if not rows:
        return "Sin leads en DB."
    lines = ["<b>🎯 Top 10 leads</b>"]
    for n, comp, email, score, status in rows:
        lines.append(f"  • <b>{score}</b> {comp[:25]} — {email[:35]} <i>[{status}]</i>")
    return "\n".join(lines)


async def _cmd_drafts() -> str:
    c = sqlite3.connect("/var/www/neuralops/leads.db").cursor()
    c.execute("SELECT id, name, company, email FROM leads WHERE status='drafted' ORDER BY score DESC LIMIT 15")
    rows = c.fetchall()
    if not rows:
        return "Sin drafts pendientes de aprobación."
    lines = [f"<b>📝 {len(rows)} drafts pendientes</b>"]
    for lid, name, comp, email in rows:
        lines.append(f"  • <code>#{lid}</code> {comp[:25]} — {email[:35]}")
    lines.append("\n<i>Usa <code>/email_drafter show &lt;id&gt;</code> para ver uno entero.</i>")
    return "\n".join(lines)


async def _cmd_help() -> str:
    return list_all_commands()


async def _cmd_ask(question: str) -> str:
    """Q&A libre con LLM + contexto del sistema."""
    if not question.strip():
        return "❓ Escribe la pregunta: <code>/ask cuántos errores hubo hoy</code>"
    from langchain_groq import ChatGroq
    llm = ChatGroq(model="llama-3.1-8b-instant", api_key=os.getenv("GROQ_API_KEY"), temperature=0.2)
    # Contexto: status actual + leads + memoria reciente
    ctx_parts = []
    try:
        d = json.loads(Path("/var/www/neuralops/agent_status.json").read_text())
        ctx_parts.append("AGENTES (estado actual):")
        for k, v in d.items():
            ctx_parts.append(f"  {k}: {v.get('level','?')} — {v.get('msg','?')[:80]}")
    except Exception:
        pass
    try:
        c = sqlite3.connect("/var/www/neuralops/state.db").cursor()
        c.execute("""SELECT json_extract(metadata, '$.agent') AS ag, document, COUNT(*)
                     FROM memory WHERE created_at >= datetime('now','-24 hours')
                     GROUP BY ag, document ORDER BY 3 DESC LIMIT 20""")
        ctx_parts.append("\nEVENTOS últimas 24h:")
        for ag, action, n in c.fetchall():
            ctx_parts.append(f"  {ag}: {action} ({n}x)")
    except Exception:
        pass

    prompt = (
        "Eres un asistente que responde sobre el sistema NeuralOps.\n\n"
        f"CONTEXTO:\n{chr(10).join(ctx_parts)}\n\n"
        f"PREGUNTA: {question}\n\n"
        "Responde en castellano, conciso (max 8 líneas), basándote SOLO en el contexto."
    )
    try:
        r = await llm.ainvoke(prompt)
        return f"<b>🤔 Respuesta</b>\n\n{r.content}"
    except Exception as e:
        return f"❌ LLM error: {e}"


# ── Procesador de mensajes texto ─────────────────────────────────────────────
async def _process_text_message(text: str) -> str | None:
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    cmd = parts[0].split("@")[0].lower()  # /agent@bot_name → /agent
    args_text = parts[1] if len(parts) > 1 else ""

    # Comandos globales
    if cmd == "/status":
        return await _cmd_status()
    if cmd == "/leads":
        return await _cmd_leads()
    if cmd == "/drafts":
        return await _cmd_drafts()
    if cmd in ("/help", "/start"):
        return await _cmd_help()
    if cmd == "/ask":
        return await _cmd_ask(args_text)

    # Comando por agente → router NLU
    return await route_command(cmd, args_text)


# ── Loop principal ───────────────────────────────────────────────────────────
async def response_handler(state: NeuralOpsState) -> NeuralOpsState:
    bot = telegram_bot.get_bot()
    try:
        offset = state.get("telegram_offset") or _load_offset()
        updates = await bot.get_updates(
            offset=offset, timeout=5,
            allowed_updates=["callback_query", "message"]
        )

        msgs_processed = 0
        cbs_processed = 0

        for update in updates:
            offset = update.update_id + 1

            # ── Mensajes de texto (comandos slash) ──────────────────────────
            if update.message and update.message.text:
                if AUTHORIZED_CHAT_ID and update.message.chat_id != AUTHORIZED_CHAT_ID:
                    continue  # ignorar mensajes de otros usuarios
                text = update.message.text
                try:
                    response = await _process_text_message(text)
                    if response:
                        await bot.send_message(
                            chat_id=update.message.chat_id,
                            text=response, parse_mode="HTML",
                            reply_to_message_id=update.message.message_id
                        )
                    msgs_processed += 1
                except Exception as e:
                    logger.error(f"[ResponseHandler] Error procesando '{text[:60]}': {e}")
                    await bot.send_message(
                        chat_id=update.message.chat_id,
                        text=f"❌ Error: {e}", parse_mode="HTML",
                        reply_to_message_id=update.message.message_id
                    )
                continue

            # ── Callbacks de botones inline ─────────────────────────────────
            cq = update.callback_query
            if not cq:
                continue
            data = cq.data or ""
            await cq.answer()

            if data.startswith("approve_action:"):
                aid = data[len("approve_action:"):]
                approve_action(aid)
                await telegram_bot.send_alert(f"✅ Acción aprobada: <code>{aid}</code>")
            elif data.startswith("reject_action:"):
                aid = data[len("reject_action:"):]
                reject_action(aid)
                await telegram_bot.send_alert(f"❌ Acción rechazada: <code>{aid}</code>")
            elif data.startswith("approve_draft:"):
                did = data[len("approve_draft:"):]
                _handle_draft_approval(did, approved=True)
                await telegram_bot.send_alert(f"✅ Borrador aprobado: <code>{did}</code>")
            elif data.startswith("reject_draft:"):
                did = data[len("reject_draft:"):]
                _handle_draft_approval(did, approved=False)
                await telegram_bot.send_alert(f"🗑 Borrador descartado: <code>{did}</code>")
            elif data.startswith("approve:"):
                cid = data[len("approve:"):]
                _handle_approval(cid, approved=True)
                await telegram_bot.send_alert(f"✅ Aprobado: <code>{cid}</code>")
            elif data.startswith("reject:"):
                cid = data[len("reject:"):]
                _handle_approval(cid, approved=False)
                await telegram_bot.send_alert(f"❌ Rechazado: <code>{cid}</code>")
            cbs_processed += 1

        state["telegram_offset"] = offset
        _save_offset(offset)

        if msgs_processed or cbs_processed:
            report("response_handler", f"{msgs_processed} mensajes + {cbs_processed} callbacks procesados", "ok")
        else:
            report("response_handler", "Escuchando Telegram — sin mensajes pendientes", "info")

    except Exception as e:
        logger.error(f"[ResponseHandler] Error al leer updates: {e}")
        report("response_handler", f"⚠ Error Telegram: {e}", "error")

    return state


def _handle_approval(conf_id: str, approved: bool):
    results = memory.query("pending_updates")
    for r in results:
        if r["id"] == conf_id:
            meta = json.loads(r.get("metadata") or "{}")
            meta["status"] = "approved" if approved else "rejected"
            memory.upsert("pending_updates", conf_id, r["document"], meta)
            return


def _handle_draft_approval(draft_id: str, approved: bool):
    results = memory.query("email_drafts", n_results=100)
    for r in results:
        if r["id"] == draft_id:
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                meta = json.loads(meta)
            new_status = "approved" if approved else "rejected"
            memory.upsert("email_drafts", draft_id, r["document"], {**meta, "status": new_status})
            return


async def run_standalone():
    from graph.state import default_state
    state = default_state()
    state["telegram_offset"] = _load_offset()
    await response_handler(state)


if __name__ == "__main__":
    asyncio.run(run_standalone())
