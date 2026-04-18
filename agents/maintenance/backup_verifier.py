"""Verifica que el backup diario del servidor se ejecutó correctamente. 06:00."""
import os
import glob
import subprocess
import asyncio
import logging
from datetime import datetime, timedelta
from core import telegram_bot, memory

logger = logging.getLogger(__name__)

BACKUP_DIRS = ["/var/backups", "/home/ubuntu/backups", "/root/backups"]
MAX_AGE_HOURS = 25


async def backup_verifier():
    found = []
    for backup_dir in BACKUP_DIRS:
        if not os.path.isdir(backup_dir):
            continue
        for f in glob.glob(f"{backup_dir}/*.tar.gz") + glob.glob(f"{backup_dir}/*.sql.gz") + glob.glob(f"{backup_dir}/*.tar"):
            mtime = datetime.fromtimestamp(os.path.getmtime(f))
            age_hours = (datetime.now() - mtime).total_seconds() / 3600
            size_mb = os.path.getsize(f) / 1024 / 1024
            found.append({"path": f, "age_hours": age_hours, "size_mb": size_mb, "mtime": mtime})

    if not found:
        await telegram_bot.send_alert(
            "🚨 <b>BackupVerifier: NO hay backups</b>\n"
            f"Directorios revisados: {', '.join(BACKUP_DIRS)}\n"
            "El cron de backup puede haber fallado."
        )
        return

    recent = [b for b in found if b["age_hours"] < MAX_AGE_HOURS and b["size_mb"] > 0]
    if not recent:
        await telegram_bot.send_alert(
            f"⚠️ <b>BackupVerifier: backup antiguo</b>\n"
            f"Último backup: {found[0]['mtime'].strftime('%Y-%m-%d %H:%M')}\n"
            f"Hace {found[0]['age_hours']:.1f}h (límite: {MAX_AGE_HOURS}h)"
        )
    else:
        b = recent[0]
        logger.info(f"[BackupVerifier] OK — {os.path.basename(b['path'])} ({b['size_mb']:.1f}MB, {b['age_hours']:.1f}h)")
        memory.log_event("backup_verifier", "backup_ok", {"file": b["path"], "size_mb": b["size_mb"]})


if __name__ == "__main__":
    asyncio.run(backup_verifier())
