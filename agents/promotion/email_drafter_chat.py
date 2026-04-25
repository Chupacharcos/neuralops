"""Handler de chat para EmailDrafter."""
import sqlite3


def _conn():
    return sqlite3.connect("/var/www/neuralops/leads.db")


async def handle_intent(intent: str, args: dict) -> str:
    if intent == "status":
        c = _conn().cursor()
        c.execute("SELECT status, COUNT(*) FROM leads GROUP BY status")
        by_status = dict(c.fetchall())
        scored_pending = by_status.get("scored", 0)
        drafted = by_status.get("drafted", 0)
        sent = by_status.get("sent", 0)
        return (
            "<b>✉️ EmailDrafter</b>\n"
            f"Drafts pendientes aprobación: <b>{drafted}</b>\n"
            f"Leads scored sin draftear: <b>{scored_pending}</b>\n"
            f"Emails ya enviados: <b>{sent}</b>"
        )

    if intent == "draft":
        n = int(args.get("n", 5))
        # Disparar el agente real (procesa todos los scored, no solo N)
        from agents.promotion.email_drafter import email_drafter
        await email_drafter()
        c = _conn().cursor()
        c.execute("SELECT COUNT(*) FROM leads WHERE status='drafted'")
        total = c.fetchone()[0]
        return f"✅ EmailDrafter ejecutado. Total drafts pendientes: <b>{total}</b>. Revisa con <code>/drafts</code>."

    if intent == "show":
        lead_id = int(args.get("id", 0))
        c = _conn().cursor()
        c.execute("SELECT name, company, email, score FROM leads WHERE id=?", (lead_id,))
        row = c.fetchone()
        if not row:
            return f"❓ Lead #{lead_id} no encontrado."
        # buscar el draft en memory
        from core import memory
        results = memory.query("email_drafts", n_results=200)
        for r in results:
            meta = r.get("metadata") or {}
            if isinstance(meta, str):
                import json as _json; meta = _json.loads(meta)
            if meta.get("lead_id") == lead_id:
                return (
                    f"<b>📧 Draft #{lead_id}</b>\n"
                    f"<b>Para:</b> {row[0]} ({row[1]}) — {row[2]} — score {row[3]}\n\n"
                    f"<pre>{r['document'][:1500]}</pre>"
                )
        return f"⚠ No hay draft generado para lead #{lead_id} todavía."

    return f"❓ Intent no soportado: <code>{intent}</code>"
