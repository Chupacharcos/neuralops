"""Redacta emails personalizados para leads con score >= 60. Solicita aprobación."""
import os
import asyncio
import logging
import json
from langchain_groq import ChatGroq
from core.leads_db import get_leads, update_lead
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

llm = ChatGroq(model="gemma2-9b-it", api_key=os.getenv("GROQ_API_KEY"), temperature=0.4)

MIN_SCORE = 60
PROJECTS_PATH = "/var/www/neuralops/projects.json"

EMAIL_TEMPLATES = {
    "Inmobiliaria": {
        "subject": "Herramienta de valoración automática para {company}",
        "context": "empresa inmobiliaria que puede usar predicción de precios con IA"
    },
    "Veterinaria / Mascotas": {
        "subject": "IA para detección de alergias felinas — {company}",
        "context": "clínica veterinaria que puede ofrecer diagnóstico asistido por IA"
    },
    "Deportes": {
        "subject": "Análisis de rendimiento deportivo con IA — {company}",
        "context": "organización deportiva que puede usar análisis predictivo"
    },
    "Fintech / E-commerce": {
        "subject": "Detección de fraude en dos etapas para {company}",
        "context": "empresa fintech que puede reducir fraude con IA"
    },
    "Arquitectura / Interiorismo": {
        "subject": "Generación automática de distribuciones 3D — {company}",
        "context": "estudio de arquitectura o interiorismo"
    },
    "default": {
        "subject": "Herramienta IA especializada para {company}",
        "context": "empresa del sector tecnológico"
    }
}

DRAFTER_PROMPT = """Escribe un email de prospección corto y directo (máx 120 palabras) para:
Empresa: {company}
Contexto: {context}
Producto: {product_name}
URL demo: {demo_url}

Tono: profesional, directo, sin ser agresivo. Menciona el producto de forma natural.
NO uses emojis. NO uses fórmulas de cortesía largas. Termina con pregunta directa.

Responde SOLO con el email, sin asunto ni firma."""


def _load_projects():
    with open(PROJECTS_PATH) as f:
        return json.load(f)


async def email_drafter():
    projects = {p["slug"]: p for p in _load_projects()}
    leads = get_leads(status="scored", min_score=MIN_SCORE, limit=10)

    if not leads:
        logger.info("[EmailDrafter] Sin leads con score suficiente")
        return

    for lead in leads:
        project = projects.get(lead["project_slug"])
        if not project:
            continue

        template = EMAIL_TEMPLATES.get(lead["sector"], EMAIL_TEMPLATES["default"])
        subject = template["subject"].format(company=lead.get("company", "vuestra empresa"))

        try:
            response = await llm.ainvoke(DRAFTER_PROMPT.format(
                company=lead.get("company", "vuestra empresa"),
                context=template["context"],
                product_name=project["name"],
                demo_url=project["demo_url"],
            ))
            body = response.content.strip()

            # Guardar borrador en memoria con status pending_approval
            draft_id = f"draft_{lead['email'].replace('@','_').replace('.','_').replace('+','_')}"
            memory.upsert("email_drafts", draft_id, body, {
                "lead_email":   lead["email"],
                "lead_company": lead.get("company", ""),
                "subject":      subject,
                "project_slug": lead["project_slug"],
                "score":        lead["score"],
                "status":       "pending_approval",
            })
            update_lead(lead["email"], status="drafted")

            # Botones inline de Telegram — response_handler los gestiona
            await telegram_bot.send_alert(
                f"✉️ <b>EmailDrafter</b> — Score: {lead['score']}/100\n"
                f"Para: <b>{lead.get('company', lead['email'])}</b> ({lead['sector']})\n"
                f"Email: <code>{lead['email']}</code>\n"
                f"Asunto: <i>{subject}</i>\n\n"
                f"<code>{body[:500]}</code>",
                buttons=[[
                    {"text": "✅ Aprobar y enviar", "data": f"approve_draft:{draft_id}"},
                    {"text": "❌ Descartar",        "data": f"reject_draft:{draft_id}"},
                ]],
            )
            logger.info(f"[EmailDrafter] borrador creado para {lead['email']}")

        except Exception as e:
            logger.error(f"[EmailDrafter] {lead['email']}: {e}")


if __name__ == "__main__":
    asyncio.run(email_drafter())
