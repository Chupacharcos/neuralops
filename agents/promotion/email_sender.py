"""Envía emails aprobados via Gmail SMTP. Máx 15/día, delay 5min entre envíos."""
import os
import asyncio
import logging
import smtplib
import uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date
from core.leads_db import get_leads, update_lead, save_email_sent
from core import telegram_bot, memory
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
EMAIL_FROM = os.getenv("EMAIL_FROM")
MAX_DAILY = 15
DELAY_BETWEEN = 300  # 5 min


def _count_sent_today() -> int:
    today = date.today().isoformat()
    results = memory.query("emails_sent_log", where={"date": today}, n_results=100)
    return len(results)


def _send_smtp(to_email: str, subject: str, body: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = to_email
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(EMAIL_FROM, to_email, msg.as_string())
        return True
    except Exception as e:
        logger.error(f"[EmailSender] SMTP error to {to_email}: {e}")
        return False


async def email_sender():
    sent_today = _count_sent_today()
    if sent_today >= MAX_DAILY:
        logger.info(f"[EmailSender] Límite diario alcanzado ({MAX_DAILY})")
        return

    # Get approved drafts from memory
    approved_drafts = memory.query("email_drafts", where={"status": "approved"}, n_results=5)

    for draft in approved_drafts:
        if sent_today >= MAX_DAILY:
            break

        meta = draft["metadata"]
        lead_email = meta.get("lead_email")
        subject = meta.get("subject", "")
        body = draft["document"]

        tracking_id = str(uuid.uuid4())[:8]
        success = _send_smtp(lead_email, subject, body)

        if success:
            sent_today += 1
            lead = get_leads(status="drafted", limit=100)
            lead_id = next((l["id"] for l in lead if l["email"] == lead_email), None)

            if lead_id:
                save_email_sent(lead_id, meta.get("project_slug", ""), subject, body, tracking_id)
            update_lead(lead_email, status="sent")
            memory.upsert("email_drafts", draft["id"], body, {**meta, "status": "sent"})
            memory.upsert("emails_sent_log", f"sent_{tracking_id}", f"sent to {lead_email}", {
                "date": date.today().isoformat(), "to": lead_email
            })

            await telegram_bot.send_alert(
                f"📨 <b>Email enviado</b>\n"
                f"A: {meta.get('lead_company', lead_email)}\n"
                f"Asunto: {subject}\n"
                f"Enviados hoy: {sent_today}/{MAX_DAILY}"
            )
            logger.info(f"[EmailSender] email enviado a {lead_email}")

            if sent_today < MAX_DAILY:
                await asyncio.sleep(DELAY_BETWEEN)
        else:
            await telegram_bot.send_alert(f"❌ <b>EmailSender fallo</b>\nNo se pudo enviar a {lead_email}")


if __name__ == "__main__":
    asyncio.run(email_sender())
