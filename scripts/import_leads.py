"""
Importación manual de leads para el pipeline de email.

Uso:
  # Añadir un lead individual
  python scripts/import_leads.py add --email "cto@empresa.com" --company "Empresa SA" \\
      --sector "Inmobiliaria" --project "prediccion-precio-inmobiliario"

  # Importar CSV (columnas: email, company, sector, project_slug)
  python scripts/import_leads.py csv leads.csv

  # Ver leads actuales
  python scripts/import_leads.py list [--status new|scored|drafted|sent]

  # Forzar re-score de leads sin score
  python scripts/import_leads.py rescore

Sectores válidos:
  Inmobiliaria | Veterinaria / Mascotas | Deportes | Fintech / E-commerce
  Arquitectura / Interiorismo | Fiscal / Legal | Salud | Finanzas
"""
import sys
import csv
import argparse
sys.path.insert(0, "/var/www/neuralops")

from dotenv import load_dotenv
load_dotenv("/var/www/neuralops/.env")

from core.leads_db import save_lead, get_leads, update_lead, _conn


VALID_SECTORS = [
    "Inmobiliaria", "Veterinaria / Mascotas", "Deportes",
    "Fintech / E-commerce", "Arquitectura / Interiorismo",
    "Fiscal / Legal", "Salud", "Finanzas", "IA / ML", "Otro",
]

# Proyectos activos (slug)
VALID_PROJECTS = [
    "value-betting", "roomcraft-ai", "metacoach", "babymind",
    "adaptive-music-engine", "chatbot-manual", "stem-splitter",
    "fraud-detector", "deteccion-zonas-revalorizacion", "alphasignal",
    "prediccion-calidad-aire", "feliniai", "prediccion-precio-inmobiliario",
    "sports-engine",
]


def cmd_add(args):
    email   = args.email.strip().lower()
    company = args.company.strip()
    sector  = args.sector.strip()
    project = args.project.strip()

    if sector not in VALID_SECTORS:
        print(f"Sector inválido. Válidos: {', '.join(VALID_SECTORS)}")
        sys.exit(1)
    if project not in VALID_PROJECTS:
        print(f"Proyecto inválido. Válidos:\n  " + "\n  ".join(VALID_PROJECTS))
        sys.exit(1)

    is_new = save_lead(
        name=getattr(args, "name", "") or "",
        company=company,
        email=email,
        sector=sector,
        project_slug=project,
        source="manual_import",
    )
    if is_new:
        print(f"✓ Lead añadido: {email} ({company}) → {project}")
    else:
        print(f"⚠ Ya existe: {email}")


def cmd_csv(args):
    added = skipped = errors = 0
    with open(args.file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            email   = row.get("email", "").strip().lower()
            company = row.get("company", "").strip()
            sector  = row.get("sector", "Otro").strip()
            project = row.get("project_slug", "chatbot-manual").strip()
            name    = row.get("name", "").strip()

            if not email or "@" not in email:
                print(f"  Fila {i}: email inválido '{email}' — saltando")
                errors += 1
                continue

            try:
                is_new = save_lead(name=name, company=company, email=email,
                                   sector=sector, project_slug=project, source="csv_import")
                if is_new:
                    added += 1
                    print(f"  ✓ {email} ({company})")
                else:
                    skipped += 1
            except Exception as e:
                print(f"  ✗ {email}: {e}")
                errors += 1

    print(f"\nResumen: {added} añadidos, {skipped} duplicados, {errors} errores")


def cmd_list(args):
    status_filter = getattr(args, "status", None)
    leads = get_leads(status=status_filter, limit=200)

    if not leads:
        print("Sin leads" + (f" con status={status_filter}" if status_filter else ""))
        return

    by_status: dict = {}
    for l in leads:
        by_status.setdefault(l["status"], []).append(l)

    for status, items in sorted(by_status.items()):
        print(f"\n── {status.upper()} ({len(items)}) ──")
        for l in items[:20]:
            score = f" score={l['score']}" if l.get("score") else ""
            print(f"  {l['email']:<40} {l.get('company',''):<30}{score}")
        if len(items) > 20:
            print(f"  ... y {len(items)-20} más")

    print(f"\nTotal: {len(leads)} leads")


def cmd_rescore(args):
    """Resetea status de leads scored a new para que el scorer los revise."""
    leads = get_leads(status="scored", limit=200)
    reset = 0
    for l in leads:
        if not l.get("score") or l["score"] == 0:
            update_lead(l["email"], status="new")
            reset += 1
    print(f"{reset} leads reseteados a 'new' para re-score")


def cmd_stats(args):
    with _conn() as conn:
        total     = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        by_status = conn.execute("SELECT status, COUNT(*) FROM leads GROUP BY status").fetchall()
        by_sector = conn.execute("SELECT sector, COUNT(*) FROM leads GROUP BY sector ORDER BY 2 DESC").fetchall()
        sent      = conn.execute("SELECT COUNT(*) FROM emails_sent").fetchone()[0]

    print(f"{'='*40}")
    print(f"Total leads: {total}")
    print(f"Emails enviados: {sent}")
    print(f"\nPor estado:")
    for row in by_status:
        print(f"  {row[0]:<15} {row[1]}")
    print(f"\nPor sector:")
    for row in by_sector:
        print(f"  {row[0]:<35} {row[1]}")


def main():
    parser = argparse.ArgumentParser(description="NeuralOps — gestión manual de leads")
    sub = parser.add_subparsers(dest="cmd")

    # add
    p_add = sub.add_parser("add", help="Añadir un lead manualmente")
    p_add.add_argument("--email",   required=True)
    p_add.add_argument("--company", required=True)
    p_add.add_argument("--sector",  required=True)
    p_add.add_argument("--project", required=True)
    p_add.add_argument("--name",    default="")

    # csv
    p_csv = sub.add_parser("csv", help="Importar leads desde CSV")
    p_csv.add_argument("file")

    # list
    p_list = sub.add_parser("list", help="Listar leads")
    p_list.add_argument("--status", default=None)

    # rescore
    sub.add_parser("rescore", help="Resetear leads sin score para re-evaluar")

    # stats
    sub.add_parser("stats", help="Estadísticas del pipeline")

    args = parser.parse_args()

    if args.cmd == "add":     cmd_add(args)
    elif args.cmd == "csv":   cmd_csv(args)
    elif args.cmd == "list":  cmd_list(args)
    elif args.cmd == "rescore": cmd_rescore(args)
    elif args.cmd == "stats": cmd_stats(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
