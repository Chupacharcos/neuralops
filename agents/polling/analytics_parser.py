"""Reads Nginx logs every 15 min. Sessions >8min on a demo → Telegram alert."""
import re
import time
import logging
from collections import defaultdict
from graph.state import NeuralOpsState
from core import telegram_bot

logger = logging.getLogger(__name__)

LOG_PATH = "/var/log/nginx/access.log"
WINDOW_SECONDS = 900   # 15 min
SESSION_ALERT_SECONDS = 480  # 8 min


def _parse_log_line(line: str) -> dict | None:
    pattern = r'(\S+) - - \[([^\]]+)\] "(\w+) ([^"]+)" (\d+) \d+ "[^"]*" "[^"]*"'
    m = re.match(pattern, line)
    if not m:
        return None
    return {"ip": m.group(1), "method": m.group(3), "path": m.group(4), "status": int(m.group(5))}


async def analytics_parser(state: NeuralOpsState) -> NeuralOpsState:
    try:
        with open(LOG_PATH, "r", errors="replace") as f:
            lines = f.readlines()[-5000:]  # last 5000 lines
    except FileNotFoundError:
        return state

    now = time.time()
    ip_sessions: dict[str, dict] = defaultdict(lambda: {"paths": [], "start": now, "demo": None})

    for line in lines:
        parsed = _parse_log_line(line)
        if not parsed or parsed["status"] not in (200, 304):
            continue

        path = parsed["path"].split("?")[0]
        ip = parsed["ip"]

        if path.startswith("/demo/"):
            slug = path.split("/demo/")[1].strip("/")
            session = ip_sessions[ip]
            session["paths"].append(path)
            if not session["demo"]:
                session["demo"] = slug

    for ip, session in ip_sessions.items():
        duration = now - session["start"]
        if duration >= SESSION_ALERT_SECONDS and session["demo"]:
            await telegram_bot.send_alert(
                f"👀 <b>Sesión activa larga</b>\n"
                f"IP: <code>{ip}</code>\n"
                f"Demo: {session['demo']}\n"
                f"Tiempo: {int(duration // 60)}min\n"
                f"Páginas visitadas: {len(session['paths'])}"
            )

    return state
