"""
Compara requirements.txt de cada proyecto contra PyPI. Noche 00:00.
- PATCH → auto-apply (pip install -U) + restart servicio
- MINOR → confirmation_queue (requiere aprobación Telegram)
- MAJOR → confirmation_queue con prioridad high + advertencia changelog
"""
import os
import asyncio
import logging
import subprocess
import httpx
from core import telegram_bot, memory
from core.confirmation_queue import async_queue_action
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


def _apply_patch_updates(project_dir: str, patches: list[str]) -> tuple[list[str], list[str]]:
    """Aplica PATCH updates con pip. Devuelve (ok_list, fail_list)."""
    ok, fail = [], []
    venv_pip = os.path.join(project_dir, "venv", "bin", "pip")
    if not os.path.exists(venv_pip):
        venv_pip = "/var/www/chatbot/venv/bin/pip"

    for entry in patches:
        # entry = "fastapi: 0.95.0 → 0.95.2"
        pkg = entry.split(":")[0].strip()
        latest = entry.split("→")[-1].strip()
        try:
            result = subprocess.run(
                [venv_pip, "install", f"{pkg}=={latest}", "--quiet"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode == 0:
                ok.append(entry)
            else:
                fail.append(f"{entry} (pip error: {result.stderr[:100]})")
        except Exception as e:
            fail.append(f"{entry} ({e})")
    return ok, fail


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

            # PATCH → auto-apply silenciosamente
            if updates["PATCH"]:
                ok, fail = _apply_patch_updates(project_dir, updates["PATCH"])
                if ok:
                    logger.info(f"[DependencyWatch] {repo} — {len(ok)} PATCH aplicados: {', '.join(ok)}")
                    memory.log_event("dependency_watch", "patch_applied",
                                     {"repo": repo, "packages": ok})
                if fail:
                    await telegram_bot.send_alert(
                        f"⚠️ <b>DependencyWatch</b>: {repo} — {len(fail)} PATCH fallidos\n" +
                        "\n".join(fail[:5])
                    )

            # MINOR → confirmation_queue (cambio de funcionalidad, necesita validar)
            if updates["MINOR"]:
                pkg_list = "\n".join(f"• {u}" for u in updates["MINOR"])
                await async_queue_action(
                    action_type="dependency_upgrade",
                    project=repo,
                    payload={"repo": repo, "project_dir": project_dir,
                             "packages": updates["MINOR"], "level": "MINOR"},
                    message=(
                        f"<b>{len(updates['MINOR'])} dependencias MINOR</b> disponibles en <code>{repo}</code>:\n\n"
                        f"{pkg_list}\n\n"
                        f"<i>MINOR puede incluir cambios de API — revisar antes de aplicar.</i>"
                    ),
                    priority="normal",
                )

            # MAJOR → confirmation_queue con prioridad alta
            if updates["MAJOR"]:
                pkg_list = "\n".join(f"• {u}" for u in updates["MAJOR"])
                await async_queue_action(
                    action_type="dependency_upgrade",
                    project=repo,
                    payload={"repo": repo, "project_dir": project_dir,
                             "packages": updates["MAJOR"], "level": "MAJOR"},
                    message=(
                        f"🚨 <b>{len(updates['MAJOR'])} dependencias MAJOR</b> en <code>{repo}</code>:\n\n"
                        f"{pkg_list}\n\n"
                        f"<b>⚠️ MAJOR: puede romper compatibilidad.</b> Revisar changelog antes de aprobar."
                    ),
                    priority="high",
                )

            memory.log_event("dependency_watch", "checked", {
                "repo": repo, "major": len(updates["MAJOR"]),
                "minor": len(updates["MINOR"]), "patch": len(updates["PATCH"])
            })


if __name__ == "__main__":
    asyncio.run(dependency_watch())
