"""Handler de chat para ProjectBuilder."""
import os
from pathlib import Path

INBOX = Path("/var/www/neuralops/projects_inbox")
DONE = Path("/var/www/neuralops/projects_inbox/implementados")
SKIP = Path("/var/www/neuralops/projects_inbox/saltados")


async def handle_intent(intent: str, args: dict) -> str:
    if intent == "status":
        if not INBOX.exists():
            return "⚠ ProjectBuilder inbox no existe."
        pending = sorted([p.name for p in INBOX.glob("*.pdf")])
        done = sorted([p.name for p in (DONE.glob("*.pdf") if DONE.exists() else [])])
        skipped = sorted([p.name for p in (SKIP.glob("*.pdf") if SKIP.exists() else [])])

        lines = ["<b>🏗 ProjectBuilder</b>"]
        lines.append(f"Pendientes: <b>{len(pending)}</b>")
        if pending[:5]:
            for p in pending[:5]: lines.append(f"  • {p}")
            if len(pending) > 5: lines.append(f"  …y {len(pending)-5} más")
        lines.append(f"\nImplementados: <b>{len(done)}</b>")
        lines.append(f"Saltados: <b>{len(skipped)}</b>")
        return "\n".join(lines)

    if intent == "skip":
        name = args.get("name", "").strip()
        if not name:
            return "❓ Indica nombre PDF: <code>/project_builder skip roomcraft</code>"
        SKIP.mkdir(parents=True, exist_ok=True)
        # buscar coincidencia parcial
        matches = [p for p in INBOX.glob("*.pdf") if name.lower() in p.name.lower()]
        if not matches:
            return f"❓ No hay PDF que coincida con '<code>{name}</code>' en el inbox."
        moved = []
        for p in matches:
            dest = SKIP / p.name
            p.rename(dest)
            moved.append(p.name)
        return f"⏭ {len(moved)} PDF saltados:\n  • " + "\n  • ".join(moved)

    return f"❓ Intent no soportado: <code>{intent}</code>"
