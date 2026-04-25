"""
Router de comandos Telegram para hablar con cada agente individualmente.

Flujo cuando el usuario escribe `/agent_name <texto libre>`:
  1. Busca el agente en INTENT_REGISTRY
  2. Si no hay texto → ejecuta intent "status" por defecto
  3. Fast path: regex match con patrones comunes (run, status, silence Nh)
  4. LLM fallback: llama-3.1-8b-instant parsea texto → {intent, args}
  5. Llama el handler del agente y devuelve string con la respuesta a Telegram
"""
import os
import re
import json
import time
import logging
import importlib
from pathlib import Path
from typing import Callable
from langchain_groq import ChatGroq
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

_llm = ChatGroq(model="llama-3.1-8b-instant", api_key=os.getenv("GROQ_API_KEY"), temperature=0)

# Cada agente declara aquí qué intents soporta + cómo se llama su handler
INTENT_REGISTRY: dict[str, dict] = {
    "health": {
        "display": "HealthAgent",
        "handler_module": "agents.polling.health_agent_chat",
        "intents": [
            {"name": "status",    "desc": "Estado actual de RAM/Swap/Disco"},
            {"name": "silence",   "desc": "Silenciar alertas N horas",  "args": [{"name": "hours", "type": "int", "default": 24}]},
            {"name": "check_now", "desc": "Forzar chequeo ahora"},
            {"name": "reset",     "desc": "Borrar cooldowns de alertas"},
        ],
    },
    "service_monitor": {
        "display": "ServiceMonitor",
        "handler_module": "agents.polling.service_monitor_chat",
        "intents": [
            {"name": "status",  "desc": "Estado de los 13 servicios demo"},
            {"name": "restart", "desc": "Reiniciar un servicio",  "args": [{"name": "service", "type": "str"}]},
            {"name": "check_now","desc": "Forzar ciclo completo ahora"},
        ],
    },
    "lead_scraper": {
        "display": "LeadScraper",
        "handler_module": "agents.promotion.lead_scraper_chat",
        "intents": [
            {"name": "status", "desc": "Última ejecución y leads obtenidos esta semana"},
            {"name": "run",    "desc": "Ejecutar scraping ahora",  "args": [{"name": "sector", "type": "str", "default": ""}]},
        ],
    },
    "email_drafter": {
        "display": "EmailDrafter",
        "handler_module": "agents.promotion.email_drafter_chat",
        "intents": [
            {"name": "status", "desc": "Drafts pendientes y leads sin draftear"},
            {"name": "draft",  "desc": "Generar N drafts ahora",  "args": [{"name": "n", "type": "int", "default": 5}]},
            {"name": "show",   "desc": "Mostrar draft #N entero",  "args": [{"name": "id", "type": "int"}]},
        ],
    },
    "seo_monitor": {
        "display": "SeoMonitor",
        "handler_module": "agents.intelligence.seo_monitor_chat",
        "intents": [
            {"name": "status", "desc": "Última ejecución y oportunidades últimos 30 días"},
            {"name": "run",    "desc": "Analizar Search Console ahora"},
        ],
    },
    "project_builder": {
        "display": "ProjectBuilder",
        "handler_module": "agents.intelligence.project_builder_chat",
        "intents": [
            {"name": "status", "desc": "Cola de PDFs pendientes y último error"},
            {"name": "skip",   "desc": "Saltar un PDF problemático",  "args": [{"name": "name", "type": "str"}]},
        ],
    },
}


# ── Fast path: patrones regex para evitar llamada LLM en casos comunes ──────
def _fast_match(text: str, intents: list[dict]) -> dict | None:
    t = text.lower().strip()

    # silence patterns: "callate", "silencio", "para alertas", "no me alertes"
    if any(k in t for k in ["callate", "silencio", "no me alert", "para alert", "no me molest"]):
        m = re.search(r"(\d+)\s*(h|hora|hour|d|dia|day)", t)
        hours = 24
        if m:
            n, unit = int(m.group(1)), m.group(2)
            hours = n * 24 if unit.startswith("d") else n
        if any(i["name"] == "silence" for i in intents):
            return {"intent": "silence", "args": {"hours": hours}}

    # run / ejecutar / corre / busca / analiza
    if any(k in t for k in ["run", "ejecut", "corre ", "lanza", "busca", "analiza", "haz"]):
        if any(i["name"] == "run" for i in intents):
            # ¿menciona sector?
            for sector in ["inmobiliar", "veterinar", "deport", "fintech", "arquitect", "salud"]:
                if sector in t:
                    return {"intent": "run", "args": {"sector": sector}}
            return {"intent": "run", "args": {}}

    # check / refresca / actualiza
    if any(k in t for k in ["check", "refresca", "actualiza", "comprueb", "ahora"]):
        if any(i["name"] == "check_now" for i in intents):
            return {"intent": "check_now", "args": {}}

    # reset / borra / limpia
    if any(k in t for k in ["reset", "borra cooldown", "limpia"]):
        if any(i["name"] == "reset" for i in intents):
            return {"intent": "reset", "args": {}}

    # restart <service>
    m = re.search(r"reinici\w*\s+(\S+)", t) or re.search(r"restart\s+(\S+)", t)
    if m and any(i["name"] == "restart" for i in intents):
        return {"intent": "restart", "args": {"service": m.group(1)}}

    # draft N
    m = re.search(r"draft\w*\s+(\d+)", t) or re.search(r"prep\w+\s+(\d+)", t) or re.search(r"haz\s+(\d+)\s+draft", t)
    if m and any(i["name"] == "draft" for i in intents):
        return {"intent": "draft", "args": {"n": int(m.group(1))}}

    # show / muestra <N>
    m = re.search(r"(?:show|muestra|enseña|ver)\s+(?:draft\s+)?(\d+)", t)
    if m and any(i["name"] == "show" for i in intents):
        return {"intent": "show", "args": {"id": int(m.group(1))}}

    return None


# ── LLM fallback: parsea texto natural a intent JSON ────────────────────────
async def _llm_parse(text: str, agent_def: dict) -> dict | None:
    intents_desc = "\n".join(
        f"- {i['name']}: {i['desc']}" + (
            " (args: " + ", ".join(f"{a['name']}:{a['type']}" for a in i.get("args", [])) + ")"
            if i.get("args") else ""
        )
        for i in agent_def["intents"]
    )

    prompt = f"""El usuario habla con el agente {agent_def['display']}. Estos son los intents disponibles:
{intents_desc}

Mensaje del usuario: "{text}"

Devuelve SOLO un JSON con: {{"intent": "<nombre>", "args": {{...}}, "confidence": 0.0-1.0}}
Si confidence < 0.6 → usa intent "unknown".
NO añadas texto fuera del JSON."""

    try:
        resp = await _llm.ainvoke(prompt)
        raw = resp.content.strip()
        # extraer JSON del posible markdown
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        parsed = json.loads(m.group(0))
        if parsed.get("intent") == "unknown" or parsed.get("confidence", 0) < 0.6:
            return None
        return {"intent": parsed["intent"], "args": parsed.get("args", {})}
    except Exception as e:
        logger.warning(f"[AgentChat] LLM parse error: {e}")
        return None


def _format_intents_help(agent_key: str, agent_def: dict) -> str:
    lines = [f"<b>/{agent_key}</b> — {agent_def['display']}"]
    for i in agent_def["intents"]:
        args = ""
        if i.get("args"):
            args = " " + " ".join(f"&lt;{a['name']}&gt;" for a in i["args"])
        lines.append(f"  • <code>{i['name']}{args}</code> — {i['desc']}")
    return "\n".join(lines)


async def route_command(command: str, args_text: str) -> str:
    """Punto de entrada principal — devuelve string para enviar a Telegram."""
    agent_key = command.lstrip("/").lower()

    if agent_key not in INTENT_REGISTRY:
        return None  # no es un comando de agente — el caller decidirá

    agent_def = INTENT_REGISTRY[agent_key]

    # Sin args → ejecutar intent "status" por defecto
    if not args_text.strip():
        parsed = {"intent": "status", "args": {}}
    else:
        parsed = _fast_match(args_text, agent_def["intents"])
        if not parsed:
            parsed = await _llm_parse(args_text, agent_def)

    if not parsed:
        return f"❓ No entendí. Acciones disponibles:\n\n{_format_intents_help(agent_key, agent_def)}"

    # Cargar handler del agente
    try:
        module = importlib.import_module(agent_def["handler_module"])
        handler: Callable = getattr(module, "handle_intent")
        result = await handler(parsed["intent"], parsed["args"])
        return result
    except ModuleNotFoundError:
        return f"⚠ Handler de chat no implementado todavía para <code>{agent_key}</code>"
    except Exception as e:
        logger.error(f"[AgentChat] handler error en {agent_key}: {e}")
        return f"❌ Error ejecutando <code>{parsed['intent']}</code>: {e}"


def list_all_commands() -> str:
    """Devuelve markdown con todos los comandos para /help."""
    lines = ["<b>📋 Comandos disponibles</b>\n"]
    lines.append("<b>Globales</b>")
    lines.append("  • <code>/status</code> — tabla de todos los agentes")
    lines.append("  • <code>/leads</code> — top leads por score")
    lines.append("  • <code>/drafts</code> — drafts pendientes con botones")
    lines.append("  • <code>/ask &lt;pregunta&gt;</code> — Q&amp;A libre con LLM")
    lines.append("  • <code>/help</code> — este menú\n")

    lines.append("<b>Por agente</b> (sin args = status, con args = acción NL)")
    for key, ag in INTENT_REGISTRY.items():
        actions = ", ".join(i["name"] for i in ag["intents"])
        lines.append(f"  • <code>/{key}</code> — {ag['display']} <i>({actions})</i>")

    lines.append("\n<b>Ejemplos</b>")
    lines.append("  <code>/health callate 24h</code>")
    lines.append("  <code>/email_drafter prepárame 5 drafts</code>")
    lines.append("  <code>/service_monitor reinicia chatbot</code>")
    lines.append("  <code>/ask qué agente lleva más errores hoy</code>")

    return "\n".join(lines)
