"""
Detecta degradación de modelos ML con el tiempo. Semanal lunes.
- Drift > 5%  → confirmation_queue(model_retrain, priority=normal)
- Drift > 10% → confirmation_queue(model_retrain, priority=high)
"""
import asyncio
import logging
import httpx
from core import telegram_bot, memory
from core.confirmation_queue import async_queue_action
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")
logger = logging.getLogger(__name__)

# Projects with trained models and their test endpoints
ML_PROJECTS = [
    {
        "slug": "prediccion-precio-inmobiliario",
        "name": "Predicción Precio Inmobiliario",
        "health_url": "http://127.0.0.1:8089/",
        "predict_url": "http://127.0.0.1:8089/ml/predict",
        "test_payload": {"habitaciones": 3, "metros": 80, "zona": "centro", "planta": 2},
        "baseline_r2": 0.88,
    },
    {
        "slug": "prediccion-calidad-aire",
        "name": "Calidad del Aire",
        "health_url": "http://127.0.0.1:8091/",
        "predict_url": "http://127.0.0.1:8091/ml/predict",
        "test_payload": {"estacion": "retiro", "hora": 12},
        "baseline_r2": 0.416,
    },
    {
        "slug": "fraud-detector",
        "name": "Fraud Detection Pipeline",
        "health_url": "http://127.0.0.1:8002/health",
        "predict_url": "http://127.0.0.1:8002/ml/predict",
        "test_payload": {"amount": 100.0, "hour": 14, "v1": 0.1},
        "baseline_auc": 0.9999,
    },
    {
        "slug": "sports-engine",
        "name": "Sports Performance Engine",
        "health_url": "http://127.0.0.1:8001/health",
        "predict_url": "http://127.0.0.1:8001/ml/predict",
        "test_payload": {"home_team": "real_madrid", "away_team": "barcelona"},
        "baseline_f1": 0.72,
    },
]


async def model_drift_detector():
    async with httpx.AsyncClient(timeout=30) as client:
        for project in ML_PROJECTS:
            try:
                # Check if service is up
                health = await client.get(project["health_url"])
                if health.status_code != 200:
                    continue

                # Test prediction endpoint
                resp = await client.post(project["predict_url"], json=project["test_payload"])
                if resp.status_code != 200:
                    await telegram_bot.send_alert(
                        f"⚠️ <b>ModelDrift</b>: {project['name']}\n"
                        f"Endpoint /predict devuelve {resp.status_code}"
                    )
                    continue

                data = resp.json()
                score = data.get("r2") or data.get("auc") or data.get("f1") or data.get("accuracy")
                baseline = project.get("baseline_r2") or project.get("baseline_auc") or project.get("baseline_f1")

                if score and baseline:
                    drop = baseline - score
                    if drop > 0.10:
                        # Crítico → confirmation_queue prioridad high
                        await async_queue_action(
                            action_type="model_retrain",
                            project=project["slug"],
                            payload={
                                "slug": project["slug"], "name": project["name"],
                                "score": round(score, 4), "baseline": baseline,
                                "drop": round(drop, 4), "detail":
                                f"Score caído de {baseline:.3f} a {score:.3f} (−{drop:.3f}). "
                                f"Reentrenar urgente con datos actualizados.",
                            },
                            message=(
                                f"🚨 <b>Drift CRÍTICO</b>: {project['name']}\n"
                                f"Baseline: <code>{baseline:.3f}</code> → Actual: <code>{score:.3f}</code>\n"
                                f"Caída: <b>{drop:.3f}</b> ({drop/baseline*100:.1f}%)\n\n"
                                f"Se recomienda reentrenamiento urgente."
                            ),
                            priority="high",
                        )
                    elif drop > 0.05:
                        # Degradación leve → confirmation_queue prioridad normal
                        await async_queue_action(
                            action_type="model_retrain",
                            project=project["slug"],
                            payload={
                                "slug": project["slug"], "name": project["name"],
                                "score": round(score, 4), "baseline": baseline,
                                "drop": round(drop, 4), "detail":
                                f"Score caído de {baseline:.3f} a {score:.3f} (−{drop:.3f}). "
                                f"Considerar reentrenamiento con datos más recientes.",
                            },
                            message=(
                                f"⚠️ <b>Drift detectado</b>: {project['name']}\n"
                                f"Baseline: <code>{baseline:.3f}</code> → Actual: <code>{score:.3f}</code>\n"
                                f"Caída: <b>{drop:.3f}</b> ({drop/baseline*100:.1f}%)"
                            ),
                            priority="normal",
                        )
                    else:
                        logger.info(f"[ModelDrift] {project['name']}: score OK ({score:.3f})")

                memory.log_event("model_drift", "checked", {
                    "slug": project["slug"], "score": score,
                    "project": project["slug"],  # para que shared_context lo encuentre por proyecto
                })

            except Exception as e:
                logger.error(f"[ModelDrift] {project['slug']}: {e}")


if __name__ == "__main__":
    asyncio.run(model_drift_detector())
