"""
Ejecuta pytest en cada proyecto. Noche 23:00.
- Tests OK → log en memory
- Sin tests → github_issue automático sugiriendo cobertura
- Tests fallan → LLM propone fix → github_issue auto con el fix
"""
import os
import asyncio
import subprocess
import logging
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from core.confirmation_queue import async_queue_action
from core.github_api import create_issue
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0)

PROJECTS = {
    "Sports-Performance-Engine": "/var/www/sports-engine",
    "fraud-detector": "/var/www/fraud-detector",
    "neuralops": "/var/www/neuralops",
}

FIX_PROMPT = """Un test de pytest está fallando. Analiza el traceback y sugiere el fix más probable.
Sé conciso — solo el fix, sin explicaciones largas.

Traceback:
{traceback}

Responde con:
1. Causa más probable (1 línea)
2. Fix propuesto (código si aplica, max 20 líneas)"""


async def test_runner():
    for repo, project_dir in PROJECTS.items():
        if not os.path.isdir(project_dir):
            continue

        tests_dir = os.path.join(project_dir, "tests")
        if not os.path.isdir(tests_dir):
            # No tests — abrir issue sugiriendo añadir
            await create_issue(
                repo=repo,
                title="[TestRunner] No hay tests — añadir cobertura básica",
                body="Este proyecto no tiene directorio `tests/`. Se recomienda añadir al menos:\n- `test_api.py` con test del endpoint /health\n- `test_model.py` con test de predicción básico",
                labels=["testing", "automated"],
            )
            continue

        venv_python = os.path.join(project_dir, "venv", "bin", "python")
        if not os.path.exists(venv_python):
            venv_python = "/var/www/chatbot/venv/bin/python"

        try:
            result = subprocess.run(
                [venv_python, "-m", "pytest", tests_dir, "-v", "--tb=short", "--timeout=60"],
                capture_output=True, text=True, timeout=120, cwd=project_dir
            )

            if result.returncode == 0:
                logger.info(f"[TestRunner] {repo}: todos los tests OK")
                memory.log_event("test_runner", "tests_ok", {"repo": repo})
                continue

            # Tests fallan → LLM propone fix → GitHub issue automático
            traceback = result.stdout[-3000:] + result.stderr[-1000:]
            response = await llm.ainvoke(FIX_PROMPT.format(traceback=traceback))
            fix_suggestion = response.content.strip()

            # Crear issue en GitHub con el fix sugerido (AUTO — no requiere confirmación)
            issue_body = (
                f"## Tests fallando — Fix sugerido por LLM\n\n"
                f"### Traceback\n```\n{traceback[-2000:]}\n```\n\n"
                f"### Fix propuesto\n{fix_suggestion}\n\n"
                f"---\n*Generado automáticamente por NeuralOps TestRunner*"
            )
            issue_url = await create_issue(
                repo=repo,
                title=f"[TestRunner] Tests fallando — fix propuesto",
                body=issue_body,
                labels=["bug", "automated", "test-failure"],
            )

            await telegram_bot.send_alert(
                f"🧪 <b>TestRunner: tests fallando</b>\n"
                f"Repo: <code>{repo}</code>\n"
                f"Issue creado con fix: {issue_url or 'error creando issue'}\n\n"
                f"<b>Fix sugerido:</b>\n<code>{fix_suggestion[:400]}</code>"
            )
            memory.log_event("test_runner", "tests_failed", {
                "repo": repo, "fix": fix_suggestion[:500],
                "issue_url": issue_url or "",
            })

        except subprocess.TimeoutExpired:
            await telegram_bot.send_alert(f"⏱️ <b>TestRunner timeout</b>: {repo} — tests >2min")
        except Exception as e:
            logger.error(f"[TestRunner] {repo}: {e}")

    from core.agent_status import report
    report("test_runner", f"Ciclo completado — {len(PROJECTS)} repos revisados", "ok")


if __name__ == "__main__":
    asyncio.run(test_runner())
