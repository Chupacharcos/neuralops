"""
ServiceMonitor — Comprueba que todos los servicios demo están vivos y detecta OOM kills.

Cada 15 min via cron:
  1. HTTP health check a cada servicio
  2. journalctl scan de OOM kills en los últimos 20 min
  3. OOM detectado → aumenta MemoryMax +25% → reinicia servicio → alerta Telegram
  4. Servicio caído (no OOM) → reinicia → alerta Telegram
  5. Reporta estado global en agent_status
"""
import re
import asyncio
import logging
import subprocess
from pathlib import Path
from core import telegram_bot, memory
from core.agent_status import report

logger = logging.getLogger(__name__)

# Puerto → (nombre servicio systemd, ruta health endpoint)
SERVICES = {
    8088: ("chatbot",            "/docs"),
    8089: ("ml-inmobiliario",    "/"),
    8090: ("ml-revalorizacion",  "/"),
    8091: ("ml-calidad-aire",    "/"),
    8001: ("sports-engine",      "/ml/sports/health"),
    8002: ("fraud-detector",     "/ml/fraud/health"),
    8003: ("value-engine",       "/ml/valuebet/health"),
    8004: ("feliniai",           "/allergy-types"),
    8005: ("alphasignal",        "/ml/signals/health"),
    8006: ("roomcraft",          "/"),
    8100: ("babymind",           "/babymind/health"),
    8101: ("metacoach",          "/"),
    8102: ("stem-splitter",      "/health"),
}

SYSTEMD_DIR = Path("/etc/systemd/system")
OOM_MEMORY_INCREASE = 0.25   # +25% cuando se detecta OOM
MAX_MEMORY_LIMIT_MB = 3000   # techo absoluto para evitar acaparar RAM


def _http_check(port: int, path: str) -> bool:
    """Devuelve True si el servicio responde HTTP 2xx/3xx/404 (cualquier respuesta = está vivo)."""
    try:
        r = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", "8", f"http://127.0.0.1:{port}{path}"],
            capture_output=True, text=True, timeout=12
        )
        code = int(r.stdout.strip() or "0")
        return 100 <= code < 600  # cualquier respuesta HTTP = proceso vivo
    except Exception:
        return False


def _get_oom_kills(service: str, minutes: int = 20) -> int:
    """Cuenta OOM kills de un servicio en los últimos N minutos."""
    try:
        r = subprocess.run(
            ["journalctl", "-u", f"{service}.service",
             f"--since={minutes} minutes ago", "--no-pager", "-q"],
            capture_output=True, text=True, timeout=10
        )
        return r.stdout.count("oom-kill") + r.stdout.count("OOM killer")
    except Exception:
        return 0


def _get_current_memory_max_mb(service: str) -> int | None:
    """Lee MemoryMax del archivo .service. Devuelve MB o None si no hay límite."""
    path = SYSTEMD_DIR / f"{service}.service"
    if not path.exists():
        return None
    text = path.read_text()
    m = re.search(r"MemoryMax=(\d+)([MmGg]?)", text)
    if not m:
        return None
    val, unit = int(m.group(1)), m.group(2).upper()
    return val * 1024 if unit == "G" else val


def _set_memory_max(service: str, new_mb: int) -> bool:
    """Reemplaza MemoryMax en el archivo .service. Devuelve True si OK."""
    path = SYSTEMD_DIR / f"{service}.service"
    if not path.exists():
        return False
    try:
        text = path.read_text()
        if re.search(r"MemoryMax=", text):
            new_text = re.sub(r"MemoryMax=\d+[MmGg]?", f"MemoryMax={new_mb}M", text)
        else:
            new_text = text.rstrip() + f"\nMemoryMax={new_mb}M\n"
        path.write_text(new_text)
        return True
    except PermissionError:
        r = subprocess.run(
            ["sudo", "bash", "-c",
             f"sed -i 's/MemoryMax=[0-9]*[MmGg]*/MemoryMax={new_mb}M/' {path}"],
            capture_output=True, timeout=10
        )
        return r.returncode == 0


def _systemctl(action: str, service: str) -> bool:
    try:
        r = subprocess.run(
            ["sudo", "systemctl", action, f"{service}.service"],
            capture_output=True, text=True, timeout=30
        )
        return r.returncode == 0
    except Exception:
        return False


async def service_monitor():
    down = []
    oom_fixed = []
    restarted = []

    for port, (svc, path) in SERVICES.items():
        alive = _http_check(port, path)
        oom_count = _get_oom_kills(svc, minutes=20)

        if oom_count > 0:
            # OOM kill detectado → aumentar MemoryMax y reiniciar
            current_mb = _get_current_memory_max_mb(svc)
            if current_mb:
                new_mb = min(int(current_mb * (1 + OOM_MEMORY_INCREASE)), MAX_MEMORY_LIMIT_MB)
            else:
                new_mb = 800  # sin límite previo → establecer base segura

            mem_set = _set_memory_max(svc, new_mb)
            _systemctl("daemon-reload", "")  # daemon-reload global
            subprocess.run(["sudo", "systemctl", "daemon-reload"], capture_output=True, timeout=15)
            ok = _systemctl("restart", svc)

            action = f"MemoryMax {current_mb}M → {new_mb}M" if (mem_set and current_mb) else f"reiniciado"
            oom_fixed.append((svc, port, oom_count, action, ok))

            await telegram_bot.send_alert(
                f"🧠 <b>ServiceMonitor — OOM kill detectado y corregido</b>\n"
                f"Servicio: <code>{svc}</code> (:{port})\n"
                f"OOM kills en 20 min: <b>{oom_count}</b>\n"
                f"Acción: <code>{action}</code>\n"
                f"Reinicio: {'✅ OK' if ok else '❌ FALLO'}"
            )
            memory.log_event("service_monitor", "oom_fixed", {
                "service": svc, "port": port, "oom_count": oom_count,
                "old_mb": current_mb, "new_mb": new_mb, "restart_ok": ok,
            })

        elif not alive:
            # Servicio caído sin OOM → reiniciar
            ok = _systemctl("restart", svc)
            restarted.append((svc, port, ok))

            await telegram_bot.send_alert(
                f"🔄 <b>ServiceMonitor — Servicio caído, reiniciando</b>\n"
                f"Servicio: <code>{svc}</code> (:{port})\n"
                f"Reinicio: {'✅ OK' if ok else '❌ FALLO — intervención manual'}"
            )
            memory.log_event("service_monitor", "service_restarted", {
                "service": svc, "port": port, "restart_ok": ok,
            })

        else:
            logger.debug(f"[ServiceMonitor] {svc}:{port} OK")

    # Resumen en agent_status
    issues = len(oom_fixed) + len(restarted)
    if issues == 0:
        msg = f"{len(SERVICES)} servicios OK"
        lvl = "ok"
    else:
        parts = []
        if oom_fixed:
            parts.append(f"{len(oom_fixed)} OOM corregidos")
        if restarted:
            parts.append(f"{len(restarted)} reiniciados")
        msg = " | ".join(parts)
        lvl = "warning"

    report("service_monitor", msg, lvl)
    memory.log_event("service_monitor", "cycle", {
        "services_checked": len(SERVICES),
        "oom_fixed": len(oom_fixed),
        "restarted": len(restarted),
    })


if __name__ == "__main__":
    asyncio.run(service_monitor())
