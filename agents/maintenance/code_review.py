"""Análisis nocturno de código — todos los proyectos. Noche 22:00."""
import os
import asyncio
import logging
import subprocess
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from core.github_api import create_issue, list_issues
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0)

REPOS = {
    "proyecto-inmobiliario": "/var/www/proyecto-inmobiliario",
    "proyecto-revalorizacion": "/var/www/proyecto-revalorizacion",
    "calidad-aire": "/var/www/calidad-aire",
    "BabyMind": "/var/www/babymind",
    "MetaCoach": "/var/www/metacoach",
    "Sports-Performance-Engine": "/var/www/sports-engine",
    "fraud-detector": "/var/www/fraud-detector",
    "value-engine": "/var/www/value-engine",
    "alphasignal": "/var/www/alphasignal",
    "roomcraft": "/var/www/roomcraft",
    "FeliniAI": "/var/www/feliniai",
    "neuralops": "/var/www/neuralops",
}

CODE_REVIEW_PROMPT = """Analiza este archivo Python y detecta SOLO problemas reales:
- Funciones >50 líneas sin lógica clara
- Queries SQL sin índice (busca SELECT sin WHERE sobre tablas grandes)
- Endpoints FastAPI sin validación Pydantic
- TODOs sin resolver que afecten funcionalidad
- Variables de 1 letra fuera de bucles
- Imports no usados

Responde SOLO si hay problemas reales. Si todo está bien, responde exactamente: "OK"
Si hay problemas, lista cada uno brevemente (max 3 líneas por problema).

Archivo: {filename}
```python
{code}
```"""


async def _review_file(filepath: str, filename: str) -> str | None:
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            code = f.read()
        if len(code) < 100:
            return None

        response = await llm.ainvoke(CODE_REVIEW_PROMPT.format(filename=filename, code=code[:6000]))
        result = response.content.strip()
        return None if result == "OK" else result
    except Exception as e:
        logger.error(f"[CodeReview] {filename}: {e}")
        return None


def _repos_by_priority() -> list[tuple[str, str]]:
    """Ordena repos priorizando proyectos con score bajo o acciones urgentes."""
    from core.shared_context import load_system_context

    ctx = load_system_context()
    top_slugs  = [p["slug"] for p in ctx["top_projects"]]   # mayor score primero
    urgent     = set(ctx["urgent_projects"])

    # Mapa slug → repo_name (clave de REPOS)
    slug_to_repo = {
        "prediccion-precio-inmobiliario": "proyecto-inmobiliario",
        "deteccion-zonas-revalorizacion": "proyecto-revalorizacion",
        "prediccion-calidad-aire":        "calidad-aire",
        "babymind":                       "BabyMind",
        "metacoach":                      "MetaCoach",
        "sports-engine":                  "Sports-Performance-Engine",
        "fraud-detector":                 "fraud-detector",
        "value-betting":                  "value-engine",
        "alphasignal":                    "alphasignal",
        "roomcraft-ai":                   "roomcraft",
        "feliniai":                       "FeliniAI",
    }

    # Urgentes primero, luego proyectos con MENOR score (los que más necesitan atención)
    repo_score: dict[str, float] = {}
    for p in ctx["top_projects"]:
        repo_name = slug_to_repo.get(p["slug"])
        if repo_name:
            repo_score[repo_name] = p["score"]

    def sort_key(item):
        repo_name = item[0]
        # Urgentes al frente
        if any(slug_to_repo.get(s) == repo_name for s in urgent):
            return (0, 0)
        # Menor score = mayor necesidad de review
        score = repo_score.get(repo_name, 50)
        return (1, score)

    return sorted(REPOS.items(), key=sort_key)


async def code_review():
    total_issues = 0

    for repo, project_dir in _repos_by_priority()[:3]:  # max 3 repos por noche (priorizados)
        if not os.path.isdir(project_dir):
            continue

        # git pull
        try:
            subprocess.run(["git", "pull"], cwd=project_dir, capture_output=True, timeout=30)
        except Exception:
            pass

        # Review Python files
        py_files = []
        for root, _, files in os.walk(project_dir):
            if any(skip in root for skip in ["venv", "__pycache__", ".git", "artifacts"]):
                continue
            for f in files:
                if f.endswith(".py"):
                    py_files.append(os.path.join(root, f))

        existing_issues = await list_issues(repo)
        existing_titles = {i["title"] for i in existing_issues}

        for filepath in py_files[:20]:  # max 20 files per repo
            filename = os.path.relpath(filepath, project_dir)
            issues_text = await _review_file(filepath, filename)
            if not issues_text:
                continue

            title = f"[CodeReview] {filename} — problemas detectados"
            if title in existing_titles:
                continue

            issue_url = await create_issue(
                repo=repo,
                title=title,
                body=f"## Análisis automático — CodeReview\n\n{issues_text}\n\n*Generado por NeuralOps*",
                labels=["code-review", "automated"],
            )
            if issue_url:
                total_issues += 1
                await telegram_bot.send_alert(
                    f"🔍 <b>CodeReview Issue</b>: {repo}\n"
                    f"Archivo: <code>{filename}</code>\n"
                    f"<a href='{issue_url}'>Ver Issue</a>"
                )

    memory.log_event("code_review", "completed", {"issues_created": total_issues})
    logger.info(f"[CodeReview] completado — {total_issues} issues creados")
    from core.agent_status import report
    if total_issues > 0:
        report("code_review", f"{total_issues} issues creados en GitHub", "warning")
    else:
        report("code_review", "Revisión completada — sin issues críticos", "ok")


if __name__ == "__main__":
    asyncio.run(code_review())
