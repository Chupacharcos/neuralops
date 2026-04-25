"""Handler de chat para HealthAgent — responde a comandos Telegram."""
import json
import time
import asyncio
import psutil
from pathlib import Path

STATE_FILE = Path("/var/www/neuralops/logs/health_alert_state.json")
SILENCE_FILE = Path("/var/www/neuralops/logs/health_silence.json")


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))


async def handle_intent(intent: str, args: dict) -> str:
    if intent == "status":
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        ram_mb = vm.available // (1024 * 1024)

        # ¿Hay silencio activo?
        silence = _load(SILENCE_FILE)
        silence_msg = ""
        if silence and silence.get("until", 0) > time.time():
            mins_left = int((silence["until"] - time.time()) / 60)
            silence_msg = f"\n🔕 Alertas silenciadas {mins_left} min más"

        state = _load(STATE_FILE)
        cooldowns = []
        for k, v in state.items():
            elapsed_h = (time.time() - v["epoch"]) / 3600
            cooldowns.append(f"  • {k}: alertó hace {elapsed_h:.1f}h")
        cooldowns_str = "\n".join(cooldowns) if cooldowns else "  ninguno activo"

        return (
            f"<b>🩺 HealthAgent</b>\n"
            f"RAM libre: <b>{ram_mb} MB</b> ({vm.percent:.0f}% usado)\n"
            f"Swap: <b>{sw.percent:.0f}%</b> usado\n"
            f"Disco: <b>{disk.percent:.0f}%</b> usado"
            f"{silence_msg}\n\n"
            f"<i>Cooldowns activos:</i>\n{cooldowns_str}"
        )

    if intent == "silence":
        hours = int(args.get("hours", 24))
        until = time.time() + hours * 3600
        _save(SILENCE_FILE, {"until": until, "set_at": time.time()})
        return f"🔕 HealthAgent silenciado <b>{hours}h</b>. Próxima alerta no antes de {time.strftime('%H:%M', time.localtime(until))}."

    if intent == "check_now":
        from agents.polling.health_agent import health_agent
        await health_agent()
        return "✅ Chequeo completo ejecutado. Revisa el status con <code>/health</code>."

    if intent == "reset":
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        return "🧹 Cooldowns limpiados. Próximo ciclo evaluará condiciones desde cero."

    return f"❓ Intent no soportado: <code>{intent}</code>"
