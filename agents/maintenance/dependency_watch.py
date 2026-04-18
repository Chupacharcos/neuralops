"""Compara requirements.txt de cada proyecto contra PyPI. Noche 00:00."""
import os
import asyncio
import logging
import httpx
from core import telegram_bot, memory
from core.github_api import create_pr

logger = logging.getLogger(__name__)

PROJECT_DIRS = {
    "proyecto-inmobiliario": "/var/www/proyecto-inmobiliario",
    "proyecto-revalorizacion": "/var/www/proyecto-revalorizacion",
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


def _parse_requirements(path: str) -> dict[str, str]:
    """Returns {package: version_spec}."""
    result = {}
    if not os.path.exists(path):
        return result
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for sep in (">=", "==", "<=", "~=", "!="):
                if sep in line:
                    pkg, ver = line.split(sep, 1)
                    result[pkg.strip().lower()] = ver.strip().split(",")[0]
                    break
            else:
                result[line.lower()] = ""
    return result


async def _get_latest_version(pkg: str, client: httpx.AsyncClient) -> str | None:
    try:
        resp = await client.get(f"https://pypi.org/pypi/{pkg}/json", timeout=10)
        if resp.status_code == 200:
            return resp.json()["info"]["version"]
    except Exception:
        pass
    return None


def _bump_type(current: str, latest: str) -> str:
    """Returns PATCH, MINOR, MAJOR or NONE."""
    try:
        c = [int(x) for x in current.split(".")[:3]]
        l = [int(x) for x in latest.split(".")[:3]]
        while len(c) < 3: c.append(0)
        while len(l) < 3: l.append(0)
        if l[0] > c[0]: return "MAJOR"
        if l[1] > c[1]: return "MINOR"
        if l[2] > c[2]: return "PATCH"
    except Exception:
        pass
    return "NONE"


async def dependency_watch():
    async with httpx.AsyncClient() as client:
        for repo, project_dir in PROJECT_DIRS.items():
            req_path = os.path.join(project_dir, "requirements.txt")
            packages = _parse_requirements(req_path)
            if not packages:
                continue

            updates = {"PATCH": [], "MINOR": [], "MAJOR": []}
            for pkg, current_ver in packages.items():
                if not current_ver:
                    continue
                latest = await _get_latest_version(pkg, client)
                if not latest:
                    continue
                bump = _bump_type(current_ver, latest)
                if bump != "NONE":
                    updates[bump].append(f"{pkg}: {current_ver} → {latest}")

            if updates["MAJOR"]:
                await telegram_bot.send_alert(
                    f"🚨 <b>DependencyWatch MAJOR</b>: {repo}\n" +
                    "\n".join(updates["MAJOR"]) +
                    "\n\n⚠️ Revisar changelog antes de actualizar."
                )

            if updates["MINOR"]:
                await telegram_bot.send_alert(
                    f"⚠️ <b>DependencyWatch MINOR</b>: {repo}\n" +
                    "\n".join(updates["MINOR"])
                )

            if updates["PATCH"]:
                logger.info(f"[DependencyWatch] {repo} — {len(updates['PATCH'])} PATCH disponibles")

            memory.log_event("dependency_watch", "checked", {
                "repo": repo, "major": len(updates["MAJOR"]),
                "minor": len(updates["MINOR"]), "patch": len(updates["PATCH"])
            })


if __name__ == "__main__":
    asyncio.run(dependency_watch())
