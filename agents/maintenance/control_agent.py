"""
Control Agent — Monitor central de NeuralOps.
- Revisa estado de todos los servicios ML (systemd)
- Analiza cron.log en busca de errores recientes
- Intenta auto-reparar fallos conocidos (restart de servicios caídos)
- Aplica auto-updates de dependencias PATCH (MINOR/MAJOR solo alerta)
- Reporta resumen vía Telegram

Corre cada 30 minutos.
"""
import os
import re
import asyncio
import logging
import subprocess
import httpx
from datetime import datetime, timedelta, timezone
from pathlib import Path
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

CRON_LOG = Path("/var/www/neuralops/logs/cron.log")

# Todos los servicios ML del portfolio
ML_SERVICES = [
    "chatbot",
    "ml-inmobiliario",
    "ml-revalorizacion",
    "ml-calidad-aire",
    "babymind",
    "metacoach",
    "stem-splitter",
    "feliniai",
    "sports-engine",
    "fraud-detector",
    "value-engine",
    "alphasignal",
    "roomcraft",
    "apis-validador",
    "apis-facturas",
    "apis-irpf",
]

# Directorio → requirements.txt para auto-patch
PROJECT_DIRS = {
    "proyecto-inmobiliario": "/var/www/proyecto-inmobiliario",
    "calidad-aire": "/var/www/calidad-aire",
    "babymind": "/var/www/babymind",
    "metacoach": "/var/www/metacoach",
    "stem-splitter": "/var/www/stem-splitter",
    "feliniai": "/var/www/feliniai",
    "sports-engine": "/var/www/sports-engine",
    "fraud-detector": "/var/www/fraud-detector",
    "value-engine": "/var/www/value-engine",
    "alphasignal": "/var/www/alphasignal",
    "roomcraft": "/var/www/roomcraft",
}

SHARED_VENV = "/var/www/chatbot/venv"

# Errores conocidos → descripción legible y si son auto-reparables
KNOWN_ERRORS = [
    (r"KeyError: 'slug'",           "KeyError slug en project_builder (texto PDF con llaves)",  False),
    (r"Connection refused",          "Servicio no disponible en el puerto",                       False),
    (r"CUDA out of memory",          "GPU sin memoria",                                           False),
    (r"ModuleNotFoundError: (.+)",   "Módulo Python no instalado",                               False),
    (r"JSONDecodeError",             "LLM devolvió JSON inválido",                               False),
    (r"Rate limit",                  "Rate limit de API externa",                                False),
    (r"Spec incompleta",             "LLM no extrajo spec completa del PDF",                     False),
]


def _service_status(name: str) -> str:
    """Returns 'active', 'failed', 'inactive' or 'unknown'."""
    try:
        r = subprocess.run(
            ["systemctl", "is-active", f"{name}.service"],
            capture_output=True, text=True, timeout=5
        )
        return r.stdout.strip()
    except Exception:
        return "unknown"


def _restart_service(name: str) -> bool:
    try:
        r = subprocess.run(
            ["sudo", "systemctl", "restart", f"{name}.service"],
            capture_output=True, text=True, timeout=30
        )
        return r.returncode == 0
    except Exception:
        return False


def _read_recent_log_errors(minutes: int = 35) -> list[dict]:
    """Parse cron.log for ERROR lines in the last N minutes."""
    if not CRON_LOG.exists():
        return []

    cutoff = datetime.now() - timedelta(minutes=minutes)
    errors = []

    with open(CRON_LOG) as f:
        lines = f.readlines()

    # Parse lines: "2026-04-20 06:00:04,421 [agent] ERROR — message"
    pattern = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[([^\]]+)\] ERROR — (.+)$")

    for line in lines:
        m = pattern.match(line.strip())
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ts < cutoff:
            continue
        errors.append({
            "ts": ts,
            "agent": m.group(2),
            "message": m.group(3),
        })

    return errors


def _classify_error(message: str) -> tuple[str, bool]:
    """Returns (description, auto_fixable)."""
    for pattern, desc, fixable in KNOWN_ERRORS:
        if re.search(pattern, message):
            return desc, fixable
    return message[:120], False


async def _check_patch_updates() -> list[str]:
    """Returns list of PATCH updates applied."""
    applied = []
    async with httpx.AsyncClient() as client:
        for repo, project_dir in PROJECT_DIRS.items():
            req_path = os.path.join(project_dir, "requirements.txt")
            if not os.path.exists(req_path):
                continue

            with open(req_path) as f:
                lines = f.readlines()

            new_lines = []
            changed = False
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    new_lines.append(line)
                    continue

                pkg, ver, sep = None, None, None
                for s in ("==", ">=", "~="):
                    if s in stripped:
                        pkg, ver = stripped.split(s, 1)
                        sep = s
                        ver = ver.split(",")[0].strip()
                        break

                if not pkg or not ver or sep not in ("==", ">="):
                    new_lines.append(line)
                    continue

                try:
                    resp = await client.get(f"https://pypi.org/pypi/{pkg.strip()}/json", timeout=8)
                    if resp.status_code != 200:
                        new_lines.append(line)
                        continue
                    latest = resp.json()["info"]["version"]
                except Exception:
                    new_lines.append(line)
                    continue

                try:
                    cv = [int(x) for x in ver.split(".")[:3]]
                    lv = [int(x) for x in latest.split(".")[:3]]
                    while len(cv) < 3: cv.append(0)
                    while len(lv) < 3: lv.append(0)
                    is_patch = (lv[0] == cv[0] and lv[1] == cv[1] and lv[2] > cv[2])
                except Exception:
                    new_lines.append(line)
                    continue

                if is_patch:
                    new_line = line.replace(f"{sep}{ver}", f"{sep}{latest}")
                    new_lines.append(new_line)
                    changed = True
                    applied.append(f"{repo}: {pkg.strip()} {ver} → {latest}")
                else:
                    new_lines.append(line)

            if changed:
                with open(req_path, "w") as f:
                    f.writelines(new_lines)

    return applied


async def control_agent():
    now_str = datetime.now().strftime("%H:%M")
    issues = []
    fixed = []

    # ── 1. Servicios caídos ──────────────────────────────────────────────────
    failed_services = []
    for svc in ML_SERVICES:
        status = _service_status(svc)
        if status == "failed":
            failed_services.append(svc)
            restarted = _restart_service(svc)
            if restarted:
                fixed.append(f"✅ Reiniciado: {svc}.service")
                memory.log_event("control_agent", "service_restarted", {"service": svc})
            else:
                issues.append(f"❌ No se pudo reiniciar: {svc}.service")
        elif status == "inactive":
            issues.append(f"⚠️ Inactivo (no failed): {svc}.service")

    # ── 2. Errores recientes en cron.log ─────────────────────────────────────
    recent_errors = _read_recent_log_errors(minutes=35)
    error_summary = []
    for err in recent_errors:
        desc, _ = _classify_error(err["message"])
        error_summary.append(f"[{err['agent']}] {desc}")

    if error_summary:
        unique_errors = list(dict.fromkeys(error_summary))  # dedup preservando orden
        issues.extend(unique_errors[:5])

    # ── 3. Auto-patch de dependencias (solo PATCH) ───────────────────────────
    patch_updates = []
    # Solo correr patch-check una vez al día (~00:30 o 12:30)
    hour = datetime.now().hour
    if hour in (0, 12):
        try:
            patch_updates = await _check_patch_updates()
        except Exception as e:
            logger.warning(f"[ControlAgent] error en patch check: {e}")

    if patch_updates:
        fixed.extend([f"📦 PATCH: {u}" for u in patch_updates[:5]])
        memory.log_event("control_agent", "patches_applied", {"count": len(patch_updates)})

    # ── 4. Reporte ───────────────────────────────────────────────────────────
    if not issues and not fixed:
        logger.info(f"[ControlAgent] {now_str} — todo OK ({len(ML_SERVICES)} servicios activos)")
        return

    lines = [f"🛡️ <b>ControlAgent</b> — {now_str}"]

    if fixed:
        lines.append("\n<b>Reparado:</b>")
        lines.extend(fixed)

    if issues:
        lines.append("\n<b>Problemas detectados:</b>")
        lines.extend(issues)

    if failed_services:
        lines.append(f"\n<i>Servicios que fallaron: {', '.join(failed_services)}</i>")

    await telegram_bot.send_alert("\n".join(lines))
    memory.log_event("control_agent", "report_sent", {
        "fixed": len(fixed), "issues": len(issues)
    })


if __name__ == "__main__":
    asyncio.run(control_agent())
