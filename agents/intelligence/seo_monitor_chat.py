"""Handler de chat para SeoMonitor."""
import json
import time
from pathlib import Path


async def handle_intent(intent: str, args: dict) -> str:
    if intent == "status":
        # Leer último estado del agent_status
        try:
            d = json.loads(Path("/var/www/neuralops/agent_status.json").read_text())
            entry = d.get("SeoMonitor", {})
            ts = entry.get("ts", "?")
            msg = entry.get("msg", "sin reportar")
        except Exception:
            ts, msg = "?", "no disponible"

        # Token GSC
        try:
            tok = json.loads(Path("/var/www/neuralops/.gsc_token.json").read_text())
            tok_status = "✅ válido" if tok.get("refresh_token") else "❌ sin refresh_token"
        except Exception:
            tok_status = "❌ no encontrado"

        return (
            f"<b>📈 SeoMonitor</b>\n"
            f"Última ejecución: {ts}\n"
            f"Resultado: {msg}\n"
            f"Token GSC: {tok_status}"
        )

    if intent == "run":
        from agents.intelligence.seo_monitor import seo_monitor
        await seo_monitor()
        return "✅ Análisis SEO ejecutado. Revisa el resumen con <code>/seo_monitor</code>."

    return f"❓ Intent no soportado: <code>{intent}</code>"
