"""
ErrorRepairAgent — Lee errores reportados en logs y toma acción correctiva.

Flujo:
  1. Parsea cron.log + neuralops.log buscando ERRORs de las últimas 6h
  2. Clasifica cada error: SERVICE_DOWN | CODE_BUG | TRANSIENT
  3. SERVICE_DOWN → systemctl restart (auto, si ≥2 fallos consecutivos)
  4. CODE_BUG → LLM propone patch → confirmation_queue (aprobación Telegram)
  5. TRANSIENT → ignorado (httpx, timeout puntual)

Corre: cada 6h via cron
"""
import os
import re
import time
import asyncio
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from core.agent_status import report
from core.confirmation_queue import async_queue_action
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0.1)

LOG_FILES = [
    Path("/var/www/neuralops/logs/cron.log"),
    Path("/var/www/neuralops/logs/neuralops.log"),
]

# Errores transitorios que no requieren acción
TRANSIENT_PATTERNS = [
    r"httpx\.TimeoutException",
    r"ConnectError",
    r"Connection refused",
    r"timeout",
    r"ETIMEDOUT",
]

# Mapeo de servicio → nombre systemd
SERVICE_MAP = {
    "chatbot":              "chatbot",
    "ml-inmobiliario":      "ml-inmobiliario",
    "ml-revalorizacion":    "ml-revalorizacion",
    "ml-calidad-aire":      "ml-calidad-aire",
    "babymind":             "babymind",
    "metacoach":            "metacoach",
    "stem-splitter":        "stem-splitter",
    "feliniai":             "feliniai",
    "sports-engine":        "sports-engine",
    "fraud-detector":       "fraud-detector",
    "value-engine":         "value-engine",
    "alphasignal":          "alphasignal",
    "roomcraft":            "roomcraft",
    "apis-validador":       "apis-validador",
}

PATCH_PROMPT = """Eres un ingeniero de software senior. Un agente Python está produciendo este error repetidamente:

AGENTE: {agent}
ERROR: {error}
CONTEXTO DEL LOG:
{context}

ARCHIVO FUENTE (fragmento relevante):
{source_snippet}

Propón un patch mínimo y concreto para corregir este error.
Responde en este formato exacto:
CAUSA: (1 línea explicando la causa raíz)
FIX: (código Python del cambio a aplicar, máx 30 líneas)
ARCHIVO: (ruta del archivo a modificar)"""


def _parse_recent_errors(hours: int = 6) -> list[dict]:
    """Lee los logs y extrae errores de las últimas N horas."""
    cutoff = datetime.now() - timedelta(hours=hours)
    errors: list[dict] = []
    pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[(.+?)\] (ERROR|WARNING) — (.+)"
    )
    for log_file in LOG_FILES:
        if not log_file.exists():
            continue
        try:
            lines = log_file.read_text(errors="replace").splitlines()
            for i, line in enumerate(lines):
                m = pattern.match(line)
                if not m:
                    continue
                ts_str, source, level, msg = m.groups()
                if level != "ERROR":
                    continue
                try:
                    ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if ts < cutoff:
                    continue
                # Contexto: las 5 líneas siguientes (traceback)
                context = "\n".join(lines[i:i+6])
                errors.append({
                    "ts": ts,
                    "source": source,
                    "msg": msg,
                    "context": context,
                    "log_file": str(log_file),
                })
        except Exception as e:
            logger.warning(f"[ErrorRepair] No se pudo leer {log_file}: {e}")
    return errors


def _is_transient(error: dict) -> bool:
    for pat in TRANSIENT_PATTERNS:
        if re.search(pat, error["msg"], re.IGNORECASE):
            return True
    return False


def _group_errors(errors: list[dict]) -> dict[str, list[dict]]:
    """Agrupa errores por agente+mensaje normalizado."""
    groups: dict[str, list[dict]] = {}
    for e in errors:
        key = f"{e['source']}::{e['msg'][:80]}"
        groups.setdefault(key, []).append(e)
    return groups


def _extract_agent_name(source: str) -> str:
    """agents.maintenance.code_review → code_review"""
    return source.split(".")[-1]


def _read_source_snippet(agent_name: str, error_msg: str) -> str:
    """Intenta leer el archivo Python del agente para dar contexto al LLM."""
    base = Path("/var/www/neuralops/agents")
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        candidate = folder / f"{agent_name}.py"
        if candidate.exists():
            src = candidate.read_text()
            # Devolver máx 60 líneas del archivo
            return "\n".join(src.splitlines()[:60])
    return "(fuente no encontrada)"


async def _repair_code_bug(agent_name: str, error_msg: str, context: str):
    """LLM analiza el error y propone un patch; se envía a confirmation_queue."""
    snippet = _read_source_snippet(agent_name, error_msg)
    try:
        resp = await llm.ainvoke(PATCH_PROMPT.format(
            agent=agent_name,
            error=error_msg[:300],
            context=context[:800],
            source_snippet=snippet,
        ))
        patch_text = resp.content.strip()
    except Exception as e:
        logger.error(f"[ErrorRepair] LLM error al generar patch: {e}")
        return

    action_id = await async_queue_action(
        action_type="major_refactor",
        project=agent_name,
        payload={"patch": patch_text, "error": error_msg[:200]},
        message=(
            f"🔧 <b>ErrorRepairAgent</b> — Bug repetido en <code>{agent_name}</code>\n\n"
            f"<b>Error:</b> <code>{error_msg[:200]}</code>\n\n"
            f"<b>Fix propuesto por LLM:</b>\n<code>{patch_text[:600]}</code>"
        ),
        priority="high",
    )
    logger.info(f"[ErrorRepair] patch propuesto para {agent_name} — action_id: {action_id}")


def _restart_service(service_name: str) -> bool:
    """Intenta reiniciar un servicio systemd. Devuelve True si OK."""
    try:
        r = subprocess.run(
            ["sudo", "systemctl", "restart", service_name],
            timeout=30, capture_output=True, text=True,
        )
        return r.returncode == 0
    except Exception as e:
        logger.error(f"[ErrorRepair] Error reiniciando {service_name}: {e}")
        return False


async def error_repair():
    errors = _parse_recent_errors(hours=6)
    if not errors:
        report("error_repair", "Sin errores en las últimas 6h", "ok")
        return

    groups = _group_errors(errors)
    repaired = 0
    proposed = 0
    skipped = 0

    for key, occurrences in groups.items():
        error = occurrences[0]
        count = len(occurrences)
        agent_name = _extract_agent_name(error["source"])

        if _is_transient(error):
            skipped += 1
            logger.debug(f"[ErrorRepair] Transitorio ignorado: {key[:60]}")
            continue

        logger.info(f"[ErrorRepair] Error repetido ({count}x): {agent_name} — {error['msg'][:80]}")

        # ── Intentar auto-reparación según tipo ──────────────────────────
        repaired_flag = False

        # 1. ¿Es un servicio caído mencionado en el error?
        for svc_key, svc_name in SERVICE_MAP.items():
            if svc_key in error["msg"] or svc_key in error["context"]:
                if count >= 2:
                    ok = _restart_service(svc_name)
                    if ok:
                        await telegram_bot.send_alert(
                            f"🔄 <b>ErrorRepairAgent</b> — Servicio reiniciado\n"
                            f"Servicio: <code>{svc_name}</code>\n"
                            f"Error original: <code>{error['msg'][:150]}</code>\n"
                            f"Ocurrencias: {count}x en 6h"
                        )
                        report("error_repair", f"Reiniciado {svc_name} tras {count} errores", "ok")
                        repaired += 1
                        repaired_flag = True
                    else:
                        await telegram_bot.send_alert(
                            f"🚨 <b>ErrorRepairAgent</b> — Fallo al reiniciar\n"
                            f"Servicio: <code>{svc_name}</code> — reinicio manual necesario"
                        )
                break

        # 2. ¿Es un bug de código Python? → Proponer patch via LLM
        if not repaired_flag and count >= 2:
            is_code_bug = any(kw in error["msg"] for kw in [
                "KeyError", "AttributeError", "TypeError", "ValueError",
                "NameError", "ImportError", "IndexError", "SyntaxError",
            ])
            if is_code_bug:
                await _repair_code_bug(agent_name, error["msg"], error["context"])
                proposed += 1
                repaired_flag = True

        if not repaired_flag:
            skipped += 1

    summary = f"{repaired} reparados | {proposed} patches propuestos | {skipped} ignorados"
    logger.info(f"[ErrorRepair] {summary}")
    level = "warning" if (repaired + proposed) > 0 else "ok"
    report("error_repair", summary, level)

    if repaired > 0 or proposed > 0:
        await telegram_bot.send_alert(
            f"🔧 <b>ErrorRepairAgent — Ciclo completado</b>\n{summary}"
        )


if __name__ == "__main__":
    asyncio.run(error_repair())
