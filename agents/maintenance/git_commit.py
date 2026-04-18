"""Push a main tras PRs aprobados. Crea tags y actualiza CHANGELOG. Tras PR ok."""
import os
import asyncio
import subprocess
import logging
from datetime import datetime
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

GH_USER = os.getenv("GITHUB_USERNAME")
GH_TOKEN = os.getenv("GITHUB_TOKEN")


def _git(cmd: list, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)


async def git_commit(repo: str, project_dir: str, message: str, tag: str = None):
    """Add, commit and push changes for a project."""
    if not os.path.isdir(project_dir):
        return

    # Configure git credentials
    remote_url = f"https://{GH_USER}:{GH_TOKEN}@github.com/{GH_USER}/{repo}.git"
    _git(["git", "remote", "set-url", "origin", remote_url], cwd=project_dir)

    _git(["git", "add", "-A"], cwd=project_dir)
    result = _git(["git", "commit", "-m", message], cwd=project_dir)

    if "nothing to commit" in result.stdout:
        logger.info(f"[GitCommit] {repo}: nothing to commit")
        return

    push = _git(["git", "push", "origin", "main"], cwd=project_dir)
    if push.returncode != 0:
        push = _git(["git", "push", "origin", "master"], cwd=project_dir)

    if tag:
        _git(["git", "tag", tag], cwd=project_dir)
        _git(["git", "push", "origin", tag], cwd=project_dir)
        logger.info(f"[GitCommit] {repo}: tag {tag} creado")

    memory.log_event("git_commit", "pushed", {"repo": repo, "message": message})
    logger.info(f"[GitCommit] {repo}: {message}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        asyncio.run(git_commit(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "chore: automated update"))
