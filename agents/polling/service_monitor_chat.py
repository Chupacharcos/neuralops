"""Handler de chat para ServiceMonitor."""
import subprocess
import asyncio


async def handle_intent(intent: str, args: dict) -> str:
    if intent == "status":
        from agents.polling.service_monitor import SERVICES, _http_check, _get_oom_kills
        ok, down, oom = [], [], []
        for port, (svc, path) in SERVICES.items():
            alive = _http_check(port, path)
            oom_count = _get_oom_kills(svc, minutes=60 * 24)  # 24h
            if oom_count > 0:
                oom.append(f"{svc} ({oom_count} OOM/24h)")
            elif not alive:
                down.append(f"{svc} :{port}")
            else:
                ok.append(svc)
        lines = [f"<b>🛡 ServiceMonitor</b>"]
        lines.append(f"✅ <b>{len(ok)}/{len(SERVICES)}</b> servicios OK")
        if down:
            lines.append(f"\n❌ Caídos:\n  • " + "\n  • ".join(down))
        if oom:
            lines.append(f"\n🧠 OOM últimas 24h:\n  • " + "\n  • ".join(oom))
        return "\n".join(lines)

    if intent == "restart":
        svc = args.get("service", "").strip()
        if not svc:
            return "❓ Indica qué servicio: <code>/service_monitor reinicia chatbot</code>"
        try:
            r = subprocess.run(
                ["sudo", "systemctl", "restart", f"{svc}.service"],
                capture_output=True, text=True, timeout=30
            )
            if r.returncode == 0:
                return f"🔄 Reiniciado: <code>{svc}.service</code>"
            return f"❌ Fallo reiniciando <code>{svc}</code>: {r.stderr[:200]}"
        except Exception as e:
            return f"❌ Error: {e}"

    if intent == "check_now":
        from agents.polling.service_monitor import service_monitor
        await service_monitor()
        return "✅ Ciclo ServiceMonitor ejecutado. Revisa el resumen con <code>/service_monitor</code>."

    return f"❓ Intent no soportado: <code>{intent}</code>"
