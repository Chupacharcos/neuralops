"""
NeuralOps LangGraph — grafo real de agentes con tool-calling.

Nodos del grafo:
  router         → decide qué acción tomar según el estado
  demo_check     → verifica disponibilidad de demos
  service_check  → verifica salud de servicios ML
  response_check → procesa callbacks Telegram
  health_check   → monitoriza recursos del servidor
  END            → termina el ciclo

Cada nodo de inteligencia usa LangChain ReAct con tools reales.
"""
from __future__ import annotations
import asyncio
import logging
import json
import os
import httpx
import time
from typing import Literal
from datetime import datetime

from langgraph.graph import StateGraph, END
from langchain_groq import ChatGroq
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from graph.state import NeuralOpsState
from core import memory
from core.agent_status import report

logger = logging.getLogger(__name__)

llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    api_key=os.getenv("GROQ_API_KEY"),
    temperature=0,
)

PROJECTS_PATH = "/var/www/neuralops/projects.json"


def _load_projects() -> list:
    with open(PROJECTS_PATH) as f:
        return json.load(f)


# ── Tools reales que los agentes LLM pueden invocar ──────────────────────────

@tool
def check_demo_health(slug: str) -> str:
    """Comprueba si una demo del portfolio está respondiendo correctamente."""
    import urllib.request
    projects = _load_projects()
    project = next((p for p in projects if p["slug"] == slug), None)
    if not project:
        return f"Proyecto {slug} no encontrado"
    url = project.get("demo_url", "")
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return f"{slug}: OK ({r.status})"
    except Exception as e:
        return f"{slug}: DOWN — {str(e)[:80]}"


@tool
def check_service_health(port: int) -> str:
    """Comprueba si un servicio FastAPI está respondiendo en el puerto dado."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            return f"Puerto {port}: OK"
    except Exception:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                return f"Puerto {port}: OK (no /health)"
        except Exception as e:
            return f"Puerto {port}: DOWN — {str(e)[:60]}"


@tool
def get_system_status() -> str:
    """Devuelve el estado general del sistema: RAM, CPU, disco."""
    from core.resource_manager import check_server_health
    s = check_server_health()
    return (
        f"RAM libre: {s['ram_free_mb']}MB | "
        f"CPU: {s['cpu_pct']}% | "
        f"Disco: {s['disk_pct']}% | "
        f"Alertas: {len(s.get('alerts', []))}"
    )


@tool
def get_pending_tasks() -> str:
    """Devuelve las acciones pendientes de aprobación o ejecución."""
    actions = memory.query("pending_actions", n_results=20)
    pending = [a for a in actions if a["metadata"].get("status") == "pending"]
    approved = [a for a in actions if a["metadata"].get("status") == "approved"]
    return (
        f"Pendientes de aprobación: {len(pending)} | "
        f"Aprobadas sin ejecutar: {len(approved)} | "
        f"Tipos: {list(set(a['metadata'].get('action_type') for a in pending[:5]))}"
    )


@tool
def get_project_scores() -> str:
    """Devuelve los scores actuales de los proyectos del portfolio."""
    scores = memory.query("project_scores", n_results=20)
    if not scores:
        return "Sin scores calculados aún"
    top = sorted(scores, key=lambda s: s["metadata"].get("total", 0), reverse=True)[:5]
    lines = [f"{s['metadata'].get('slug','?')}: {s['metadata'].get('total',0):.0f}/100" for s in top]
    return "Top proyectos:\n" + "\n".join(lines)


@tool
def get_leads_summary() -> str:
    """Devuelve el resumen del pipeline de leads."""
    from core.leads_db import get_leads
    from collections import Counter
    leads = get_leads(limit=200)
    statuses = Counter(l["status"] for l in leads)
    return f"Total leads: {len(leads)} | Por estado: {dict(statuses)}"


@tool
def log_system_event(agent: str, event: str, detail: str) -> str:
    """Registra un evento en la memoria del sistema para que otros agentes lo lean."""
    memory.log_event(agent, event, {"detail": detail, "ts": datetime.now().isoformat()})
    return f"Evento registrado: {agent}/{event}"


@tool
def update_agent_status(agent_name: str, message: str, level: str) -> str:
    """Actualiza el estado visible de un agente en la house view."""
    report(agent_name, message, level)
    return f"Status actualizado: {agent_name} → {message}"


# ── Nodos del grafo ───────────────────────────────────────────────────────────

ROUTER_SYSTEM = """Eres el orquestador de NeuralOps, un sistema de agentes autónomos.
Tu trabajo es analizar el estado del sistema y decidir qué verificación hacer primero.

Estado actual del sistema:
{state_summary}

Herramientas disponibles:
- check_demo_health: verifica una demo específica
- check_service_health: verifica un servicio ML
- get_system_status: estado de recursos del servidor
- get_pending_tasks: tareas pendientes de aprobación
- get_project_scores: scores del portfolio
- get_leads_summary: pipeline de leads
- log_system_event: registra eventos para otros agentes
- update_agent_status: actualiza estado visible

Responde con una de estas acciones en JSON:
{{"action": "demo_check"|"service_check"|"health_check"|"response_check"|"done",
  "reason": "por qué elegiste esta acción"}}"""


async def router_node(state: NeuralOpsState) -> NeuralOpsState:
    """Nodo router: decide qué verificar según el estado actual."""
    demo_failures = state.get("demo_failures", {})
    last_errors   = state.get("last_errors", {})

    state_summary = (
        f"Demos con fallos: {sum(1 for v in demo_failures.values() if v > 0)}\n"
        f"Errores de agentes: {len(last_errors)}\n"
        f"Hora: {datetime.now().strftime('%H:%M')}\n"
        f"Ciclo: {state.get('cycle_count', 0)}"
    )

    messages = [
        SystemMessage(content=ROUTER_SYSTEM.format(state_summary=state_summary)),
        HumanMessage(content="¿Qué debe verificar el sistema en este ciclo?"),
    ]
    try:
        resp = await llm.ainvoke(messages)
        content = resp.content.strip()
        import re
        m = re.search(r'\{.*?\}', content, re.DOTALL)
        if m:
            decision = json.loads(m.group())
            action = decision.get("action", "done")
            reason = decision.get("reason", "")
            state["router_decision"] = action
            logger.info(f"[Graph] router → {action} | {reason[:80]}")
        else:
            state["router_decision"] = "done"
            logger.info(f"[Graph] router → done (no JSON in response)")
    except Exception as e:
        logger.warning(f"[Graph] router error: {e}")
        state["router_decision"] = "done"

    state["cycle_count"] = state.get("cycle_count", 0) + 1
    return state


async def demo_check_node(state: NeuralOpsState) -> NeuralOpsState:
    """Verifica la disponibilidad de las demos usando tools reales."""
    from agents.polling.demo_watcher import demo_watcher
    return await demo_watcher(state)


async def service_check_node(state: NeuralOpsState) -> NeuralOpsState:
    """Verifica la salud de los servicios ML."""
    from agents.polling.performance_watch import performance_watch
    return await performance_watch(state)


async def health_check_node(state: NeuralOpsState) -> NeuralOpsState:
    """Verifica recursos del servidor."""
    from agents.maintenance.health_agent import health_agent
    return await health_agent(state)


async def response_check_node(state: NeuralOpsState) -> NeuralOpsState:
    """Procesa callbacks de Telegram."""
    from agents.polling.response_handler import response_handler
    return await response_handler(state)


def route_decision(state: NeuralOpsState) -> Literal["demo_check", "service_check", "health_check", "response_check", "end"]:
    decision = state.get("router_decision", "done")
    if decision == "demo_check":
        return "demo_check"
    elif decision == "service_check":
        return "service_check"
    elif decision == "health_check":
        return "health_check"
    elif decision == "response_check":
        return "response_check"
    return "end"


# ── Construcción del grafo ────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(NeuralOpsState)

    g.add_node("router",         router_node)
    g.add_node("demo_check",     demo_check_node)
    g.add_node("service_check",  service_check_node)
    g.add_node("health_check",   health_check_node)
    g.add_node("response_check", response_check_node)

    g.set_entry_point("router")

    g.add_conditional_edges("router", route_decision, {
        "demo_check":     "demo_check",
        "service_check":  "service_check",
        "health_check":   "health_check",
        "response_check": "response_check",
        "end":            END,
    })

    # Después de cada verificación, vuelve al router para decidir qué sigue
    g.add_edge("demo_check",     "router")
    g.add_edge("service_check",  "router")
    g.add_edge("health_check",   "router")
    g.add_edge("response_check", "router")

    return g.compile()


_graph = None

def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def run_graph_cycle(state: NeuralOpsState, max_steps: int = 6) -> NeuralOpsState:
    """Ejecuta el grafo hasta max_steps o hasta que el router decida 'done'."""
    graph = get_graph()
    steps = 0
    async for chunk in graph.astream(state):
        steps += 1
        if steps >= max_steps:
            break
    return state
