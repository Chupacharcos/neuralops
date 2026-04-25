"""Monitoriza RAM, disco y swap del servidor. Corre cada 15 min via cron.

Política de alertas (anti-spam):
- Cada condición tiene cooldown de 6h: no re-alerta si ya alertó en esa ventana
- Re-alerta inmediata si la situación EMPEORA significativamente (>15% peor)
- Re-alerta inmediata si una condición NUEVA aparece (no estaba en la última alerta)
- Cuando una condición se recupera (vuelve por debajo del umbral) → reset cooldown
"""
import json
import time
import asyncio
import logging
from pathlib import Path
import psutil
from core import telegram_bot, memory
from core.agent_status import report

logger = logging.getLogger(__name__)

RAM_ALERT_MB   = 300       # antes 400 — bajado: la VPS opera siempre cerca del límite
SWAP_ALERT_PCT = 95        # antes 80 — solo alertar si swap casi lleno (>95%)
DISK_ALERT_PCT = 90        # antes 85 — más margen

ALERT_COOLDOWN_SEC      = 6 * 3600   # 6h sin re-alertar misma condición
WORSENING_THRESHOLD_PCT = 15         # % de empeoramiento para forzar nueva alerta

STATE_FILE = Path("/var/www/neuralops/logs/health_alert_state.json")


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state))


def _should_alert(condition_key: str, current_value: float, state: dict) -> tuple[bool, str]:
    """Decide si alertar una condición concreta. Devuelve (alert?, motivo)."""
    now = time.time()
    prev = state.get(condition_key)

    if not prev:
        return True, "nueva condición"

    elapsed = now - prev["epoch"]
    prev_val = prev["value"]

    # Si pasó el cooldown completo → re-alertar
    if elapsed >= ALERT_COOLDOWN_SEC:
        return True, f"cooldown expirado ({elapsed/3600:.1f}h)"

    # Si la condición empeoró >15% respecto a la última alerta → re-alertar
    if condition_key == "ram":
        # menos RAM = peor. Empeora si current < prev × (1 - 0.15)
        if current_value < prev_val * (1 - WORSENING_THRESHOLD_PCT / 100):
            return True, f"RAM empeoró: {prev_val}MB → {current_value}MB"
    else:
        # más swap/disk = peor
        if current_value > prev_val * (1 + WORSENING_THRESHOLD_PCT / 100):
            return True, f"empeoró: {prev_val:.0f}% → {current_value:.0f}%"

    return False, "dentro de cooldown"


async def health_agent():
    vm   = psutil.virtual_memory()
    sw   = psutil.swap_memory()
    disk = psutil.disk_usage("/")

    ram_free_mb = vm.available // (1024 * 1024)
    swap_pct    = sw.percent
    disk_pct    = disk.percent

    state = _load_state()
    alerts_to_send = []
    new_state = dict(state)

    # ── RAM ──────────────────────────────────────────────────────────
    if ram_free_mb < RAM_ALERT_MB:
        should, reason = _should_alert("ram", ram_free_mb, state)
        if should:
            alerts_to_send.append(f"🔴 RAM libre: {ram_free_mb} MB (umbral {RAM_ALERT_MB} MB) — {reason}")
            new_state["ram"] = {"epoch": time.time(), "value": ram_free_mb}
    elif "ram" in state:
        # Recuperado → reset cooldown
        new_state.pop("ram", None)

    # ── Swap ─────────────────────────────────────────────────────────
    if swap_pct > SWAP_ALERT_PCT:
        should, reason = _should_alert("swap", swap_pct, state)
        if should:
            alerts_to_send.append(f"🟡 Swap: {swap_pct:.0f}% usado (umbral {SWAP_ALERT_PCT}%) — {reason}")
            new_state["swap"] = {"epoch": time.time(), "value": swap_pct}
    elif "swap" in state:
        new_state.pop("swap", None)

    # ── Disco ────────────────────────────────────────────────────────
    if disk_pct > DISK_ALERT_PCT:
        should, reason = _should_alert("disk", disk_pct, state)
        if should:
            alerts_to_send.append(f"🔴 Disco: {disk_pct:.0f}% usado (umbral {DISK_ALERT_PCT}%) — {reason}")
            new_state["disk"] = {"epoch": time.time(), "value": disk_pct}
    elif "disk" in state:
        new_state.pop("disk", None)

    if alerts_to_send:
        await telegram_bot.send_alert(
            "⚠️ <b>HealthAgent — Alerta de sistema</b>\n\n" + "\n".join(alerts_to_send)
        )
        _save_state(new_state)
    elif new_state != state:
        # No alertas pero hubo recuperaciones → guardar nuevo estado
        _save_state(new_state)

    # Status interno (no a Telegram, solo dashboard)
    is_critical = (ram_free_mb < RAM_ALERT_MB or swap_pct > SWAP_ALERT_PCT or disk_pct > DISK_ALERT_PCT)
    status_msg = f"RAM: {ram_free_mb}MB libre | Swap: {swap_pct:.0f}% | Disco: {disk_pct:.0f}%"
    report("health_agent", status_msg, "warning" if is_critical else "ok")

    memory.log_event("health_agent", "checked", {
        "ram_free_mb": ram_free_mb,
        "swap_pct": round(swap_pct, 1),
        "disk_pct": round(disk_pct, 1),
        "alerts_sent": len(alerts_to_send),
    })


if __name__ == "__main__":
    asyncio.run(health_agent())
