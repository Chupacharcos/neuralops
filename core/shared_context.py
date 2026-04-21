"""
Shared context loader — cualquier agente importa esto para tener
situational awareness del sistema antes de actuar.

Uso:
    from core.shared_context import load_system_context, load_project_context
    ctx = load_system_context()
    proj = load_project_context("fraud-detector")
"""
from __future__ import annotations
from core import memory


def load_system_context() -> dict:
    """
    Devuelve el último estado del sistema:
    - Informe semanal del MetaAgent (si existe)
    - Top 5 proyectos por score
    - Proyectos con acciones urgentes pendientes
    """
    # Último informe semanal
    weekly = memory.query("system_context", n_results=1)
    weekly_data = weekly[0]["metadata"] if weekly else {}

    # Top proyectos por score
    scores = memory.query("project_scores", n_results=50)
    top_projects = sorted(
        [{"slug": r["metadata"].get("project"), "score": r["metadata"].get("total", 0),
          "name": r["metadata"].get("name", ""), "month": r["metadata"].get("month", "")}
         for r in scores if r["metadata"].get("project")],
        key=lambda x: x["score"],
        reverse=True,
    )[:5]

    # Proyectos con acciones urgentes (pending_actions priority=high)
    urgent = memory.query("pending_actions", n_results=20)
    urgent_slugs = [
        r["metadata"].get("project")
        for r in urgent
        if r["metadata"].get("priority") == "high"
        and r["metadata"].get("status") == "pending"
    ]

    # Proyectos con prioridad de promoción explícita
    promo_priority = memory.query("promotion_priority", n_results=5)
    priority_slugs = [r["metadata"].get("slug") for r in promo_priority
                      if r["metadata"].get("status") == "active"]

    return {
        "weekly": weekly_data,
        "top_projects": top_projects,
        "urgent_projects": urgent_slugs,
        "promotion_priority": priority_slugs,
        "system_healthy": weekly_data.get("demo_alerts", 0) < 3,
        "emails_this_week": weekly_data.get("emails_sent", 0),
    }


def load_project_context(slug: str) -> dict:
    """
    Devuelve el contexto completo de un proyecto:
    - Score actual y dimensiones
    - Recomendaciones del evaluador
    - Acciones pendientes
    - Últimos eventos relevantes
    """
    scores = memory.query("project_scores", where={"project": slug}, n_results=1)
    score_data = scores[0]["metadata"] if scores else {}

    actions = memory.query("pending_actions", where={"project": slug}, n_results=10)
    pending = [a for a in actions if a["metadata"].get("status") == "pending"]
    approved = [a for a in actions if a["metadata"].get("status") == "approved"]
    done_count = sum(1 for a in actions if a["metadata"].get("status") == "done")

    events = memory.query("events", where={"project": slug}, n_results=5)

    return {
        "slug": slug,
        "score": score_data.get("total", 0),
        "dimensions": score_data.get("dimensions", {}),
        "recommendations": score_data.get("recommendations", ""),
        "month": score_data.get("month", ""),
        "pending_actions": pending,
        "approved_actions": approved,
        "actions_done": done_count,
        "recent_events": [e["document"] for e in events],
    }


def get_best_project_to_promote(projects_json: list, already_posted: set | None = None) -> dict | None:
    """
    Selecciona el mejor proyecto para promocionar considerando:
    1. Proyectos con prioridad explícita del recommendation_router
    2. Proyectos con mayor score del ProjectEvaluator
    3. Proyectos no promocionados recientemente

    projects_json: lista de proyectos de projects.json (ya filtrados por activo=True)
    already_posted: set de slugs ya publicados (para evitar repetir)
    """
    ctx = load_system_context()
    already_posted = already_posted or set()

    active_slugs = {p["slug"] for p in projects_json}

    # 1. Prioridad explícita del router
    for slug in ctx["promotion_priority"]:
        if slug in active_slugs and slug not in already_posted:
            match = next((p for p in projects_json if p["slug"] == slug), None)
            if match:
                return match

    # 2. Proyectos urgentes con score alto (no posted)
    for slug in ctx["urgent_projects"]:
        if slug in active_slugs and slug not in already_posted:
            match = next((p for p in projects_json if p["slug"] == slug), None)
            if match:
                return match

    # 3. Mejor score no posteado
    scores = {r["slug"]: r["score"] for r in ctx["top_projects"] if r["slug"] in active_slugs}
    ranked = sorted(
        [p for p in projects_json if p["slug"] not in already_posted],
        key=lambda p: scores.get(p["slug"], 0),
        reverse=True,
    )
    if ranked:
        return ranked[0]

    # 4. Reiniciar cola (todos han sido publicados)
    ranked_all = sorted(projects_json, key=lambda p: scores.get(p["slug"], 0), reverse=True)
    return ranked_all[0] if ranked_all else None
