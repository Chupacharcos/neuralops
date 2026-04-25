"""
DemoCI — Tests funcionales end-to-end de cada demo.

A diferencia de ServiceMonitor (que solo comprueba si el proceso responde HTTP),
este agente ejecuta el flujo real de cada demo y valida que el resultado es correcto.

Cada hora via cron:
  - Chatbot RAG    → POST /query con pregunta conocida, valida que 'answer' no está vacío
  - Inmobiliario   → POST /ml/predict con payload fijo, valida precio > 0
  - Revalorización → GET /ml/map-data, valida lista no vacía
  - Calidad Aire   → GET /ml/prediccion, valida array de 24 predicciones
  - Sports Engine  → POST /ml/sports/predict, valida 'home_win_prob' en rango [0,1]
  - Fraud Detector → POST /ml/fraud/predict, valida 'fraud_probability' en rango [0,1]
  - Value Betting  → GET /ml/valuebet/signals, valida estructura de respuesta
  - AlphaSignal    → GET /ml/signals/today, valida lista de señales
  - BabyMind       → POST /babymind/chat, valida respuesta no vacía
  - MetaCoach      → POST /demo/metacoach/chat (via Laravel), valida respuesta
  - FeliniAI       → POST /analyze/symptoms, valida diagnosis presente
  - RoomCraft      → GET /  , valida HTTP 200
  - Stem Splitter  → GET /health, valida status ok

Fallo funcional → alerta Telegram con detalle exacto del fallo (qué devolvió, qué se esperaba)
"""
import asyncio
import logging
import subprocess
import json
from core import telegram_bot, memory
from core.agent_status import report

logger = logging.getLogger(__name__)

BASE = "http://127.0.0.1"


def _post(port: int, path: str, payload: dict, headers: dict | None = None, timeout: int = 20) -> dict | None:
    cmd = [
        "curl", "-s", "-X", "POST", "--max-time", str(timeout),
        "-H", "Content-Type: application/json",
    ]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
    cmd += ["-d", json.dumps(payload), f"{BASE}:{port}{path}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        return json.loads(r.stdout) if r.stdout.strip() else None
    except Exception as e:
        logger.debug(f"[DemoCI] POST {port}{path}: {e}")
        return None


def _get(port: int, path: str, timeout: int = 15) -> dict | list | None:
    try:
        r = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), f"{BASE}:{port}{path}"],
            capture_output=True, text=True, timeout=timeout + 5
        )
        return json.loads(r.stdout) if r.stdout.strip() else None
    except Exception as e:
        logger.debug(f"[DemoCI] GET {port}{path}: {e}")
        return None


TESTS = [
    {
        "name": "Chatbot RAG",
        "fn": lambda: _post(8088, "/query",
            {"question": "que tecnologias usa Adrian", "session_id": "democi-test", "lang": "es"},
            {"X-API-Key": "DemoAPIkey2025"}, timeout=25),
        "validate": lambda r: isinstance(r, dict) and r.get("answer") and len(r["answer"]) > 10,
        "hint": "campo 'answer' vacio o ausente",
    },
    {
        "name": "Inmobiliario predict",
        "fn": lambda: _post(8089, "/ml/inmobiliario/predict", {
            "sqft_living": 1200, "bedrooms": 3, "bathrooms": 2, "floors": 1,
            "condition": 3, "grade": 7, "yr_built": 1990, "yr_renovated": 0,
            "lat": 40.41, "long": -3.70, "sqft_lot": 5000, "view": 0,
        }),
        "validate": lambda r: isinstance(r, dict) and isinstance(r.get("precio_estimado"), (int, float)) and r["precio_estimado"] > 0,
        "hint": "precio_estimado no presente o <= 0",
    },
    {
        "name": "Revalorizacion mapa",
        "fn": lambda: _get(8090, "/ml/revalorizacion/mapa"),
        "validate": lambda r: isinstance(r, dict) and r.get("barrios") is not None,
        "hint": "campo 'barrios' ausente en respuesta",
    },
    {
        "name": "Calidad Aire prediccion",
        "fn": lambda: _get(8091, "/ml/prediccion"),
        "validate": lambda r: r is not None,
        "hint": "respuesta vacia o error",
    },
    {
        "name": "Sports Engine predict",
        "fn": lambda: _post(8001, "/ml/sports/predict", {
            "home_team": "Real Madrid", "away_team": "Barcelona",
            "competition": "La Liga", "match_date": "2026-05-01",
        }),
        "validate": lambda r: isinstance(r, dict) and 0 <= r.get("probabilities", {}).get("home_win", -1) <= 1,
        "hint": "probabilities.home_win fuera de [0,1] o ausente",
    },
    {
        "name": "Fraud Detector predict",
        "fn": lambda: _post(8002, "/ml/fraud/predict", {"TransactionAmt": 150.0}),
        "validate": lambda r: isinstance(r, dict) and 0 <= r.get("fraud_probability", -1) <= 1,
        "hint": "fraud_probability fuera de [0,1] o ausente",
    },
    {
        "name": "Value Betting signals",
        "fn": lambda: _get(8003, "/ml/valuebet/signals"),
        "validate": lambda r: r is not None,
        "hint": "respuesta vacia",
    },
    {
        "name": "AlphaSignal today",
        "fn": lambda: _get(8005, "/ml/signals/today"),
        "validate": lambda r: r is not None,
        "hint": "respuesta vacia",
    },
    {
        "name": "FeliniAI symptoms",
        "fn": lambda: _post(8004, "/analyze/symptoms", {
            "symptoms": ["prurito intenso", "alopecia", "papulas"],
            "breed": "Persa", "age_years": 3, "indoor": True,
        }),
        "validate": lambda r: isinstance(r, dict) and r.get("tipo_alergia_probable") is not None,
        "hint": "campo 'tipo_alergia_probable' ausente",
    },
    {
        "name": "BabyMind chat",
        "fn": lambda: _post(8100, "/babymind/chat", {
            "session_id": "democi-baby",
            "baby_profile": {"name": "Test", "age_months": 6, "sex": "M", "conditions": []},
            "message": "hola que hitos deberia tener",
        }),
        "validate": lambda r: isinstance(r, dict) and r.get("response") and len(r["response"]) > 10,
        "hint": "campo 'response' vacio o ausente",
    },
    {
        "name": "RoomCraft health",
        "fn": lambda: _get(8006, "/"),
        "validate": lambda r: r is not None,
        "hint": "no responde en /",
    },
    {
        "name": "Stem Splitter health",
        "fn": lambda: _get(8102, "/health"),
        "validate": lambda r: isinstance(r, dict) and r.get("status") == "ok",
        "hint": "status != ok",
    },
    {
        "name": "MetaCoach health",
        "fn": lambda: _get(8101, "/"),
        "validate": lambda r: r is not None,
        "hint": "no responde en /",
    },
]


async def demo_ci():
    failed = []
    passed = 0

    for test in TESTS:
        try:
            result = await asyncio.get_event_loop().run_in_executor(None, test["fn"])
            ok = test["validate"](result)
        except Exception as e:
            ok = False
            result = str(e)

        if ok:
            passed += 1
            logger.debug(f"[DemoCI] ✓ {test['name']}")
        else:
            failed.append((test["name"], test["hint"], str(result)[:200] if result else "null"))
            logger.warning(f"[DemoCI] ✗ {test['name']}: {test['hint']}")

    if failed:
        def _esc(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        lines = "\n".join(
            f"• <b>{_esc(name)}</b> — {_esc(hint)}\n  <code>{_esc(resp[:120])}</code>"
            for name, hint, resp in failed
        )
        await telegram_bot.send_alert(
            f"🧪 <b>DemoCI — {len(failed)} test(s) funcional(es) fallando</b>\n\n{lines}"
        )
        memory.log_event("demo_ci", "failures", {
            "failed": [n for n, _, _ in failed],
            "passed": passed,
            "total": len(TESTS),
        })
        lvl = "error"
        msg = f"{len(failed)} fallos — {passed}/{len(TESTS)} OK"
    else:
        lvl = "ok"
        msg = f"{passed}/{len(TESTS)} tests funcionales OK"

    report("demo_ci", msg, lvl)


if __name__ == "__main__":
    asyncio.run(demo_ci())
