"""
ProjectBuilder — Lee PDFs de /var/www/proyectos, implementa el proyecto completo
(FastAPI + blade view + seeder + nginx + systemd) y lo mueve a proyectos_implementados.
Corre cada 6 horas via cron.
"""
import os, asyncio, logging, json, re, socket, subprocess, time, shutil
from pathlib import Path
from langchain_groq import ChatGroq
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=os.getenv("GROQ_API_KEY"), temperature=0.2)

INBOX_DIR     = Path("/var/www/proyectos")
DONE_DIR      = INBOX_DIR / "proyectos_implementados"
PROJECTS_JSON = Path("/var/www/neuralops/projects.json")
PORTFOLIO_DIR = Path("/var/www/portfolio")
VENV          = "/var/www/chatbot/venv"  # shared venv por defecto

# ── Convenciones del portfolio ──────────────────────────────────────────────

CONVENTIONS = """
CONVENCIONES DEL PORTFOLIO (seguir estrictamente):

ESTRUCTURA BACKEND (FastAPI):
- Directorio: /var/www/{slug}/
- Archivos mínimos: api.py, router.py (o routers/{slug}.py)
- api.py: FastAPI app con CORS, monta el router, tiene GET /health que devuelve {"status":"ok","service":"{slug}","port":{port}}
- router.py: endpoints del proyecto bajo prefix="/ml" o "/demo"
- El servidor corre con: uvicorn api:app --host 127.0.0.1 --port {port}

STACK PYTHON:
- Venv compartido: /var/www/chatbot/venv (salvo deps muy específicas)
- LLM: langchain_groq + ChatGroq(model="llama-3.3-70b-versatile") para features de IA conversacional
- ML: scikit-learn, lightgbm, xgboost, torch (ya instalados en venv compartido)
- Datos: siempre sintéticos si no hay dataset real (numpy para generarlos)

SERVICIO systemd:
- Archivo: /etc/systemd/system/{slug}.service
- User=ubuntu, WorkingDirectory=/var/www/{slug}
- MemoryMax=200M, OOMScoreAdj=500
- ExecStart=/var/www/chatbot/venv/bin/uvicorn api:app --host 127.0.0.1 --port {port}

NGINX:
- Añadir en /etc/nginx/sites-available/adrianmoreno-dev.com dentro del server block:
  location /api/{slug}/ { proxy_pass http://127.0.0.1:{port}/; ... }

BLADE VIEW:
- Archivo: /var/www/portfolio/resources/views/demos/{slug}.blade.php
- Standalone HTML (NO @extends layout), mismos colores: bg=#0a192f, accent=#64ffda
- Incluye demo interactiva conectada al backend via fetch() a /api/{slug}/

LARAVEL SEEDER:
- Clase PHP en /var/www/portfolio/database/seeders/{Name}Seeder.php
- Usa Project::updateOrCreate(['slug'=>'{slug}'], [...campos...])
- Campos: titulo, descripcion_corta, descripcion_larga (HTML), metricas (array), ventajas (array),
  tecnologias (array), imagen_principal (string), url_demo, url_github, destacado, orden, demo_type, categoria

RUTAS LARAVEL (añadir en routes/web.php):
- GET  /demo/{slug}/... → controladores específicos del proyecto
- Ya existe: Route::get('/demo/{slug}', [DemoChatbotController::class, 'show']) (NO duplicar)

CONTROLLER:
- En DemoChatbotController@show hay un match(demo_type) — añadir el nuevo caso

CATEGORÍAS VÁLIDAS: IA / ML, Salud, Finanzas, Arquitectura, Música, Deporte, Educación
"""

SPEC_PROMPT = """Analiza este documento de especificación de proyecto y extrae la información.

DOCUMENTO:
{text}

Responde SOLO con JSON válido sin markdown, sin comentarios:
{{
  "nombre": "Nombre completo del proyecto",
  "slug": "nombre-en-kebab-case-sin-espacios",
  "demo_type": "slug-del-demo-para-match-en-laravel",
  "categoria": "una de: IA / ML | Salud | Finanzas | Arquitectura | Música | Deporte | Educación",
  "descripcion_corta": "1 frase, máx 200 chars",
  "descripcion_larga_html": "<h2>Título</h2><p>descripción completa en HTML limpio</p>...",
  "metricas": {{"Métrica 1": "valor", "Métrica 2": "valor"}},
  "ventajas": ["ventaja1", "ventaja2", "ventaja3"],
  "tecnologias": ["Tech1", "Tech2", "Tech3"],
  "sector": "sector para NeuralOps",
  "has_model": true,
  "url_github": "https://github.com/Chupacharcos/nombre-repo",
  "orden": 105,
  "endpoints_demo": [
    {{"method": "POST", "path": "/demo/{{slug}}/predict", "description": "qué hace"}}
  ]
}}"""

BACKEND_PROMPT = """Genera el código Python completo para el backend FastAPI de este proyecto.

ESPECIFICACIÓN DEL PROYECTO:
{spec_json}

TEXTO COMPLETO DEL PDF:
{text}

{conventions}

Responde SOLO con JSON válido sin markdown:
{{
  "api_py": "contenido completo de api.py",
  "router_py": "contenido completo de router.py con todos los endpoints",
  "train_py": "contenido completo de train.py (genera datos sintéticos si no hay dataset real, entrena y guarda artifacts/) o null si no aplica",
  "requirements_extra": ["dep1==x.y", "dep2"]
}}

IMPORTANTE:
- El código debe ser funcional y completo (sin TODOs ni placeholders)
- Usa datos sintéticos realistas con numpy/sklearn si el proyecto necesita un modelo
- El endpoint /health DEBE devolver {{"status": "ok", "service": "{slug}", "port": {port}}}
- Los endpoints de demo deben retornar datos realistas aunque sean simulados
"""

FRONTEND_PROMPT = """Genera la vista blade completa para este proyecto del portfolio.

ESPECIFICACIÓN:
{spec_json}

ENDPOINTS DISPONIBLES EN EL BACKEND:
{endpoints}

{conventions}

Genera el archivo blade completo. REGLAS ESTRICTAS:
- Standalone HTML (NO @extends, NO @section)
- Colores: background #0a192f, accent #64ffda, texto #8892b0, cards #112240
- Fuente: Inter via Google Fonts
- Demo interactiva: formulario HTML → fetch() a los endpoints → muestra resultado
- Sin Tailwind CDN (usar <style> con CSS custom)
- Responsive (mobile-first)
- El blade debe empezar con <!DOCTYPE html> y ser auto-contenido

Responde SOLO con el contenido del archivo blade, sin ningún wrapper ni explicación."""

SEEDER_PROMPT = """Genera el seeder PHP de Laravel para este proyecto.

ESPECIFICACIÓN:
{spec_json}

IMAGEN: proyectos/{slug}-card.png (puede no existir aún, no importa)

Genera la clase PHP completa. Debe seguir exactamente este patrón:
<?php
namespace Database\\Seeders;
use App\\Models\\Project;
use Illuminate\\Database\\Seeder;
class {ClassName}Seeder extends Seeder {{
    public function run(): void {{
        Project::updateOrCreate(
            ['slug' => '{slug}'],
            [... todos los campos ...]
        );
    }}
}}

Responde SOLO con el código PHP, sin explicaciones."""


# ── Utilidades ───────────────────────────────────────────────────────────────

def _run(cmd, cwd=None, timeout=60):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _next_port() -> int:
    used = set()
    if PROJECTS_JSON.exists():
        for p in json.loads(PROJECTS_JSON.read_text()):
            if p.get("api_port"):
                used.add(p["api_port"])
    port = 8007
    while port in used or not _port_free(port):
        port += 1
    return port


def _extract_json(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    end   = text.rfind("}") + 1
    if start < 0:
        raise ValueError("No JSON found in LLM response")
    return json.loads(text[start:end])


def _read_pdf(pdf_path: Path) -> str:
    import pdfplumber
    pages = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                pages.append(t)
    return "\n\n".join(pages)


# ── Fases de implementación ──────────────────────────────────────────────────

async def _summarize_changes(text: str, spec: dict) -> str:
    prompt = (
        f"Este PDF describe cambios a un proyecto existente llamado '{spec.get('nombre', spec.get('slug', '?'))}'.\n"
        f"Resume en 3-5 bullets concisos qué cambios propone el documento. "
        f"Responde SOLO los bullets, sin introducción.\n\nDOCUMENTO:\n{text[:4000]}"
    )
    resp = await llm.ainvoke(prompt)
    return resp.content.strip()[:800]


async def _analyze_spec(text: str) -> dict:
    safe_text = text[:8000].replace("{", "{{").replace("}", "}}")
    resp = await llm.ainvoke(SPEC_PROMPT.format(text=safe_text))
    data = _extract_json(resp.content)
    required = ["nombre", "slug", "demo_type", "categoria", "descripcion_corta"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"Spec incompleta — faltan campos: {missing}. LLM devolvió: {list(data.keys())}")
    return data


async def _generate_backend(spec: dict, text: str, port: int) -> dict:
    safe_text = text[:6000].replace("{", "{{").replace("}", "}}")
    prompt = BACKEND_PROMPT.format(
        spec_json=json.dumps(spec, ensure_ascii=False, indent=2),
        text=safe_text,
        conventions=CONVENTIONS,
        slug=spec["slug"],
        port=port,
    )
    resp = await llm.ainvoke(prompt)
    return _extract_json(resp.content)


async def _generate_frontend(spec: dict, port: int) -> str:
    endpoints_desc = "\n".join(
        f"- {e['method']} /api/{spec['slug']}{e['path'].replace('/demo/' + spec['slug'], '')} → {e['description']}"
        for e in spec.get("endpoints_demo", [])
    ) or f"- POST /api/{spec['slug']}/predict → inferencia principal"

    prompt = FRONTEND_PROMPT.format(
        spec_json=json.dumps(spec, ensure_ascii=False, indent=2),
        endpoints=endpoints_desc,
        conventions=CONVENTIONS,
        slug=spec["slug"],
    )
    resp = await llm.ainvoke(prompt)
    content = resp.content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
    return content


async def _generate_seeder(spec: dict) -> str:
    class_name = "".join(w.capitalize() for w in re.split(r"[-_\s]", spec["slug"])) + "Seeder"
    prompt = SEEDER_PROMPT.format(
        spec_json=json.dumps(spec, ensure_ascii=False, indent=2),
        slug=spec["slug"],
        ClassName=class_name,
    )
    resp = await llm.ainvoke(prompt)
    content = resp.content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
    return content, class_name


def _deploy_backend(slug: str, port: int, backend: dict) -> list[str]:
    """Escribe ficheros, crea servicio, levanta. Retorna lista de errores."""
    errors = []
    project_dir = Path(f"/var/www/{slug}")
    project_dir.mkdir(parents=True, exist_ok=True)

    # Escribir api.py y router.py
    (project_dir / "api.py").write_text(backend["api_py"], encoding="utf-8")
    (project_dir / "router.py").write_text(backend["router_py"], encoding="utf-8")

    if backend.get("train_py"):
        (project_dir / "train.py").write_text(backend["train_py"], encoding="utf-8")

    # Instalar deps extra en venv compartido
    for dep in (backend.get("requirements_extra") or []):
        code, _, err = _run([f"{VENV}/bin/pip", "install", dep, "-q"])
        if code != 0:
            errors.append(f"pip install {dep}: {err[:80]}")

    # Crear .gitignore
    gitignore = "venv/\n__pycache__/\nartifacts/\n*.log\n.env\n"
    (project_dir / ".gitignore").write_text(gitignore)

    # Crear artifacts/ si hay modelo
    (project_dir / "artifacts").mkdir(exist_ok=True)

    # Entrenar si hay train.py
    if backend.get("train_py") and (project_dir / "train.py").exists():
        logger.info(f"[ProjectBuilder] Entrenando modelo para {slug}...")
        code, out, err = _run([f"{VENV}/bin/python", "train.py"], cwd=str(project_dir), timeout=300)
        if code != 0:
            errors.append(f"train.py falló: {err[:120]}")

    # Crear systemd service
    service_content = f"""[Unit]
Description={slug} API
After=network.target

[Service]
MemoryMax=200M
OOMScoreAdj=500
Type=simple
User=ubuntu
WorkingDirectory=/var/www/{slug}
Environment="PATH={VENV}/bin"
ExecStart={VENV}/bin/uvicorn api:app --host 127.0.0.1 --port {port}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
    service_path = Path(f"/etc/systemd/system/{slug}.service")
    try:
        service_path.write_text(service_content)
        _run(["sudo", "systemctl", "daemon-reload"])
        _run(["sudo", "systemctl", "enable", f"{slug}.service"])
        _run(["sudo", "systemctl", "start",  f"{slug}.service"])
    except Exception as e:
        errors.append(f"systemd: {e}")

    # Nginx location block
    nginx_block = f"""
    location /api/{slug}/ {{
        proxy_pass http://127.0.0.1:{port}/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 60s;
    }}
"""
    nginx_conf = Path("/etc/nginx/sites-available/adrianmoreno-dev.com")
    if nginx_conf.exists():
        content = nginx_conf.read_text()
        marker = "# END LOCATIONS"
        if marker in content and f"location /api/{slug}/" not in content:
            content = content.replace(marker, nginx_block + marker)
            nginx_conf.write_text(content)
            _run(["sudo", "nginx", "-t"])
            _run(["sudo", "systemctl", "reload", "nginx"])

    return errors


def _deploy_frontend(slug: str, blade_content: str, seeder_content: str, class_name: str, spec: dict) -> list[str]:
    errors = []

    # Blade view
    blade_path = PORTFOLIO_DIR / "resources/views/demos" / f"{slug}.blade.php"
    blade_path.write_text(blade_content, encoding="utf-8")

    # Seeder
    seeder_path = PORTFOLIO_DIR / "database/seeders" / f"{class_name}.php"
    seeder_path.write_text(seeder_content, encoding="utf-8")

    # Correr seeder
    code, _, err = _run(
        ["php", "artisan", "db:seed", f"--class={class_name}"],
        cwd=str(PORTFOLIO_DIR), timeout=30
    )
    if code != 0:
        errors.append(f"seeder: {err[:120]}")

    # Añadir caso en DemoChatbotController match()
    controller_path = PORTFOLIO_DIR / "app/Http/Controllers/DemoChatbotController.php"
    ctrl = controller_path.read_text(encoding="utf-8")
    demo_type = spec.get("demo_type", slug)
    if f"'{demo_type}'" not in ctrl:
        new_case = f"            '{demo_type}'   => 'demos.{slug}',\n"
        ctrl = ctrl.replace("            default          => 'demo',", new_case + "            default          => 'demo',")
        controller_path.write_text(ctrl, encoding="utf-8")

    # Limpiar vistas compiladas
    _run(["php", "artisan", "view:clear"], cwd=str(PORTFOLIO_DIR))

    return errors


def _test_health(port: int, retries: int = 6, delay: int = 5) -> bool:
    import urllib.request
    for _ in range(retries):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def _update_projects_json(spec: dict, port: int):
    projects = json.loads(PROJECTS_JSON.read_text()) if PROJECTS_JSON.exists() else []
    slug = spec["slug"]
    if not any(p["slug"] == slug for p in projects):
        projects.append({
            "slug":        slug,
            "name":        spec["nombre"],
            "demo_url":    f"https://adrianmoreno-dev.com/demo/{slug}",
            "health_url":  f"http://127.0.0.1:{port}/health",
            "api_port":    port,
            "sector":      spec.get("sector", "General"),
            "has_model":   spec.get("has_model", False),
            "github_repo": spec.get("url_github", "").split("/")[-1],
            "keywords":    spec.get("tecnologias", [])[:5],
        })
        PROJECTS_JSON.write_text(json.dumps(projects, indent=2, ensure_ascii=False))


# ── Main ─────────────────────────────────────────────────────────────────────

async def project_builder():
    DONE_DIR.mkdir(exist_ok=True)
    pdfs = [f for f in INBOX_DIR.glob("*.pdf") if f.is_file()]

    if not pdfs:
        logger.debug("[ProjectBuilder] Sin PDFs en inbox")
        return

    for pdf_path in pdfs:
        logger.info(f"[ProjectBuilder] Procesando: {pdf_path.name}")

        # Notificar inicio
        await telegram_bot.send_alert(
            f"📄 <b>ProjectBuilder</b> — procesando <code>{pdf_path.name}</code>\n"
            f"Esto puede tardar varios minutos..."
        )

        try:
            # 1. Leer PDF
            text = _read_pdf(pdf_path)
            if len(text) < 200:
                await telegram_bot.send_alert(f"⚠️ PDF vacío o ilegible: {pdf_path.name}")
                continue

            # 2. Analizar spec
            spec = await _analyze_spec(text)
            slug = spec["slug"]
            port = _next_port()
            logger.info(f"[ProjectBuilder] spec extraída: {slug} → puerto {port}")

            # ── ¿Es una actualización de proyecto existente? ──────────────
            existing = json.loads(PROJECTS_JSON.read_text()) if PROJECTS_JSON.exists() else []
            is_update = any(p["slug"] == slug for p in existing)

            if is_update:
                # ¿Ya hay una aprobación pendiente registrada?
                pending = memory.query("pending_updates", where={"slug": slug, "status": "approved"})
                if not pending:
                    # Pedir confirmación — extraer resumen de cambios
                    changes_summary = await _summarize_changes(text, spec)
                    conf_id = f"update_{slug}_{int(time.time())}"
                    memory.upsert("pending_updates", conf_id, pdf_path.name, {
                        "slug": slug, "pdf": str(pdf_path), "status": "pending",
                        "changes": changes_summary,
                    })
                    await telegram_bot.send_confirmation({
                        "type": "project_update",
                        "id": conf_id,
                        "message": (
                            f"📝 <b>Actualización de proyecto existente</b>: <code>{slug}</code>\n\n"
                            f"<b>Cambios propuestos</b>:\n{changes_summary}\n\n"
                            f"¿Implementar estos cambios?"
                        ),
                    })
                    logger.info(f"[ProjectBuilder] Update de {slug} pendiente de aprobación ({conf_id})")
                    continue  # esperar respuesta en próximo ciclo

                # Aprobado — marcar como en proceso y continuar como update
                approved_record = pending[0]
                memory.upsert("pending_updates", approved_record["id"], pdf_path.name, {
                    **json.loads(approved_record.get("metadata") or "{}"),
                    "status": "implementing",
                })
                logger.info(f"[ProjectBuilder] Update de {slug} aprobado — implementando")

            # 3. Generar backend
            backend = await _generate_backend(spec, text, port)

            # 4. Generar frontend
            blade  = await _generate_frontend(spec, port)
            seeder, class_name = await _generate_seeder(spec)

            # 5. Desplegar backend
            b_errors = _deploy_backend(slug, port, backend)

            # 6. Test health (esperar hasta 30s)
            healthy = _test_health(port)

            if not healthy:
                await telegram_bot.send_alert(
                    f"❌ <b>ProjectBuilder</b> — {slug} no pasa el health check\n"
                    f"Errores backend: {'; '.join(b_errors) or 'ninguno'}\n"
                    f"Revisa: journalctl -u {slug}.service -n 30"
                )
                memory.log_event("project_builder", "health_failed", {"slug": slug})
                continue

            # 7. Desplegar frontend
            f_errors = _deploy_frontend(slug, blade, seeder, class_name, spec)

            # 8. Actualizar projects.json
            _update_projects_json(spec, port)

            # 9. Mover PDF a implementados
            shutil.move(str(pdf_path), str(DONE_DIR / pdf_path.name))

            all_errors = b_errors + f_errors
            await telegram_bot.send_alert(
                f"✅ <b>ProjectBuilder — {spec['nombre']}</b>\n\n"
                f"Puerto: {port} · Demo type: {spec.get('demo_type', slug)}\n"
                f"Demo: https://adrianmoreno-dev.com/demo/{slug}\n"
                f"GitHub: {spec.get('url_github', 'N/A')}\n\n"
                + (f"⚠️ Advertencias: {'; '.join(all_errors[:3])}" if all_errors else "🟢 Sin errores")
            )
            memory.log_event("project_builder", "project_built", {"slug": slug, "port": port})
            logger.info(f"[ProjectBuilder] {slug} implementado correctamente en puerto {port}")
            from core.agent_status import report
            report("project_builder", f"Implementado: {spec['nombre']} → puerto {port}", "ok")

        except Exception as e:
            logger.error(f"[ProjectBuilder] Error con {pdf_path.name}: {e}", exc_info=True)
            await telegram_bot.send_alert(
                f"❌ <b>ProjectBuilder error</b> — {pdf_path.name}\n<code>{str(e)[:200]}</code>"
            )
            from core.agent_status import report
            report("project_builder", f"⚠ Error procesando {pdf_path.name}: {str(e)[:60]}", "error")


if __name__ == "__main__":
    asyncio.run(project_builder())
