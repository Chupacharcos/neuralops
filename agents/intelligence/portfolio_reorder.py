"""
PortfolioReorder — reordena los proyectos de la web según los scores del ProjectEvaluator.
Se ejecuta automáticamente tras ProjectEvaluator (primer lunes del mes, 07:30)
y puede lanzarse manualmente: python neuralops_cron.py portfolio_reorder
"""
import os
import asyncio
import logging
import pymysql
import pymysql.cursors
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
load_dotenv("/var/www/portfolio/.env")  # Lee credenciales DB de Laravel

logger = logging.getLogger(__name__)

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USERNAME", "portfolio_user"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_DATABASE", "portfolio_db"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}

# Proyectos fijos que SIEMPRE van primero independientemente del score
# (demos flagship o proyectos con valor estratégico especial)
PINNED_FIRST: list[str] = []  # añadir slugs aquí si se quiere fijar alguno

# Top N proyectos por score reciben destacado=1
TOP_DESTACADOS = 6


def _get_db():
    return pymysql.connect(**DB_CONFIG)


def _get_portfolio_projects() -> list[dict]:
    with _get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, titulo, slug, orden, destacado FROM projects ORDER BY orden")
            return cur.fetchall()


def _get_scores_from_memory() -> dict[str, float]:
    """Lee todos los scores del ProjectEvaluator desde memory."""
    results = memory.query("project_scores", n_results=50)
    scores = {}
    for r in results:
        meta = r["metadata"]
        slug = meta.get("project")
        total = meta.get("total")
        if slug and total is not None:
            # If multiple months, keep the highest (most recent)
            if slug not in scores or total > scores[slug]:
                scores[slug] = float(total)
    return scores


def _compute_new_order(projects: list[dict], scores: dict[str, float]) -> list[dict]:
    """
    Calcula el nuevo orden:
    1. Proyectos pinned primero (orden 10, 20...)
    2. Proyectos con score, ordenados de mayor a menor (orden 100, 200...)
    3. Proyectos sin score al final, orden original preservado (orden 900+)
    """
    pinned = [p for p in projects if p["slug"] in PINNED_FIRST]
    scored = [p for p in projects if p["slug"] not in PINNED_FIRST and p["slug"] in scores]
    unscored = [p for p in projects if p["slug"] not in PINNED_FIRST and p["slug"] not in scores]

    # Sort scored projects by score descending
    scored.sort(key=lambda p: scores[p["slug"]], reverse=True)

    ordered = pinned + scored + unscored

    # Assign new orden values (multiples of 10 for easy manual adjustments)
    result = []
    for i, project in enumerate(ordered):
        new_orden = (i + 1) * 10
        new_destacado = 1 if i < TOP_DESTACADOS else 0
        result.append({
            **project,
            "new_orden": new_orden,
            "new_destacado": new_destacado,
            "score": scores.get(project["slug"]),
        })
    return result


def _apply_order(ordered_projects: list[dict]) -> int:
    """Aplica el nuevo orden en la DB. Devuelve número de filas actualizadas."""
    changes = 0
    with _get_db() as conn:
        with conn.cursor() as cur:
            for p in ordered_projects:
                if p["new_orden"] != p["orden"] or p["new_destacado"] != p["destacado"]:
                    cur.execute(
                        "UPDATE projects SET orden=%s, destacado=%s, updated_at=NOW() WHERE id=%s",
                        (p["new_orden"], p["new_destacado"], p["id"]),
                    )
                    changes += 1
        conn.commit()
    return changes


async def portfolio_reorder():
    try:
        projects = _get_portfolio_projects()
        scores = _get_scores_from_memory()

        if not scores:
            logger.info("[PortfolioReorder] Sin scores en memoria — ejecuta ProjectEvaluator primero")
            await telegram_bot.send_alert(
                "ℹ️ <b>PortfolioReorder</b>: sin scores disponibles.\n"
                "Ejecuta primero ProjectEvaluator para generar el ranking."
            )
            return

        ordered = _compute_new_order(projects, scores)
        changes = _apply_order(ordered)

        # Build summary for Telegram
        lines = ["🔀 <b>PortfolioReorder — Nuevo orden de proyectos</b>\n"]
        for i, p in enumerate(ordered, 1):
            score_str = f" ({p['score']:.0f}pts)" if p["score"] else " (sin score)"
            star = "⭐" if p["new_destacado"] else "  "
            changed = "← cambio" if p["new_orden"] != p["orden"] else ""
            lines.append(f"{i:2}. {star} {p['titulo'][:35]}{score_str} {changed}")

        report = "\n".join(lines)
        await telegram_bot.send_alert(
            f"{report}\n\n"
            f"<i>{changes} proyectos reordenados · Top {TOP_DESTACADOS} marcados como destacados</i>"
        )

        memory.log_event("portfolio_reorder", "reordered", {
            "projects_reordered": changes,
            "total_projects": len(projects),
            "scored_projects": len(scores),
        })
        logger.info(f"[PortfolioReorder] {changes} proyectos actualizados en DB")

    except Exception as e:
        logger.error(f"[PortfolioReorder] error: {e}", exc_info=True)
        await telegram_bot.send_alert(f"❌ <b>PortfolioReorder error</b>: {e}")


if __name__ == "__main__":
    asyncio.run(portfolio_reorder())
