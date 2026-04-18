"""GitHubSync — git push de todos los proyectos con cambios + README auto-generado. Diario 04:00."""
import os
import asyncio
import logging
import json
import subprocess
from pathlib import Path
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0.2)

PROJECTS_PATH = "/var/www/neuralops/projects.json"
GH_TOKEN = os.getenv("GITHUB_TOKEN")
GH_USER  = os.getenv("GITHUB_USERNAME")

README_PROMPT = """Genera un README.md profesional para este proyecto de IA/ML de portfolio.

Proyecto: {name}
Descripción: {description}
Stack técnico: {tech}
Demo URL: {demo_url}
Puerto API: {port}

El README debe incluir:
- Título y descripción atractiva
- Sección "¿Qué hace?" (2-3 párrafos)
- Sección "Stack técnico" (lista)
- Sección "Demo en vivo" con el enlace
- Sección "Arquitectura" (brief)
- Sección "Instalación" (comandos para levantar en local)
- Badge de estado

Usa Markdown limpio. Sin placeholders, todo concreto. Máx 120 líneas."""


def _run(cmd: list, cwd: str = None, timeout: int = 30) -> tuple[int, str, str]:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _has_changes(repo_path: str) -> bool:
    code, out, _ = _run(["git", "status", "--porcelain"], cwd=repo_path)
    return code == 0 and bool(out)


def _get_remote_url(repo_path: str) -> str | None:
    code, out, _ = _run(["git", "remote", "get-url", "origin"], cwd=repo_path)
    return out if code == 0 else None


def _set_remote_with_token(repo_path: str, repo_name: str):
    url = f"https://{GH_TOKEN}@github.com/{GH_USER}/{repo_name}.git"
    _run(["git", "remote", "set-url", "origin", url], cwd=repo_path)


async def _generate_readme(project: dict) -> str:
    try:
        resp = await llm.ainvoke(README_PROMPT.format(
            name=project.get("name", project["slug"]),
            description=project.get("description", "Proyecto IA del portfolio"),
            tech=", ".join(project.get("tech_stack", [])) or "Python · FastAPI · ML",
            demo_url=project.get("demo_url", f"https://adrianmoreno-dev.com/demo/{project['slug']}"),
            port=project.get("api_port", "N/A"),
        ))
        return resp.content.strip()
    except Exception as e:
        logger.error(f"[GitHubSync] README generation error: {e}")
        return f"# {project.get('name', project['slug'])}\n\nProyecto IA — [Ver demo](https://adrianmoreno-dev.com/demo/{project['slug']})\n"


async def _sync_repo(project: dict) -> dict:
    slug = project["slug"]
    repo_name = project.get("github_repo", slug)
    repo_path = f"/var/www/{slug}"

    if not Path(repo_path).is_dir() or not (Path(repo_path) / ".git").is_dir():
        return {"slug": slug, "status": "skip", "reason": "no git repo"}

    # Generate/update README if missing or older than 30 days
    readme_path = Path(repo_path) / "README.md"
    needs_readme = not readme_path.exists()

    if needs_readme:
        logger.info(f"[GitHubSync] generando README para {slug}")
        readme_content = await _generate_readme(project)
        readme_path.write_text(readme_content, encoding="utf-8")

    if not _has_changes(repo_path):
        return {"slug": slug, "status": "clean", "reason": "no changes"}

    # Ensure token in remote URL
    _set_remote_with_token(repo_path, repo_name)

    # Stage, commit, push
    _run(["git", "add", "-A"], cwd=repo_path)
    commit_msg = "chore: auto-sync — NeuralOps GitHubSync"
    if needs_readme:
        commit_msg = "docs: add auto-generated README — NeuralOps GitHubSync"

    code, _, err = _run(["git", "commit", "-m", commit_msg], cwd=repo_path)
    if code != 0:
        return {"slug": slug, "status": "error", "reason": f"commit failed: {err[:100]}"}

    code, _, err = _run(["git", "push", "origin", "HEAD"], cwd=repo_path, timeout=60)
    if code != 0:
        return {"slug": slug, "status": "error", "reason": f"push failed: {err[:100]}"}

    return {"slug": slug, "status": "pushed", "readme": needs_readme}


async def github_sync():
    with open(PROJECTS_PATH) as f:
        projects = json.load(f)

    results = []
    for project in projects:
        try:
            result = await _sync_repo(project)
            results.append(result)
            logger.info(f"[GitHubSync] {result['slug']}: {result['status']}")
        except Exception as e:
            logger.error(f"[GitHubSync] {project['slug']}: {e}")
            results.append({"slug": project["slug"], "status": "error", "reason": str(e)[:80]})

    # Also sync neuralops itself
    try:
        _set_remote_with_token("/var/www/neuralops", "neuralops")
        if _has_changes("/var/www/neuralops"):
            _run(["git", "add", "-A"], cwd="/var/www/neuralops")
            _run(["git", "commit", "-m", "chore: auto-sync NeuralOps — GitHubSync"], cwd="/var/www/neuralops")
            _run(["git", "push", "origin", "HEAD"], cwd="/var/www/neuralops", timeout=60)
            results.append({"slug": "neuralops", "status": "pushed"})
    except Exception as e:
        logger.error(f"[GitHubSync] neuralops: {e}")

    pushed  = [r for r in results if r["status"] == "pushed"]
    errors  = [r for r in results if r["status"] == "error"]
    readmes = [r for r in pushed if r.get("readme")]

    if pushed or errors:
        lines = [f"🔄 <b>GitHubSync completado</b>\n"]
        if pushed:
            lines.append(f"✅ Pusheados ({len(pushed)}): {', '.join(r['slug'] for r in pushed)}")
        if readmes:
            lines.append(f"📝 READMEs generados: {', '.join(r['slug'] for r in readmes)}")
        if errors:
            lines.append(f"❌ Errores ({len(errors)}): {', '.join(r['slug'] for r in errors)}")
        await telegram_bot.send_alert("\n".join(lines))

    memory.log_event("github_sync", "completed", {
        "pushed": len(pushed), "errors": len(errors), "readmes": len(readmes)
    })


if __name__ == "__main__":
    asyncio.run(github_sync())
