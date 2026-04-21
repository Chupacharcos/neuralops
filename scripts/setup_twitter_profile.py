"""
Setup automático del perfil de Twitter/X para la presentación del portfolio.

Actualiza: nombre, bio, localización, web y foto de portada (banner).
El banner se genera con FLUX.1-schnell (http://127.0.0.1:8098).

Uso:
    cd /var/www/neuralops
    python scripts/setup_twitter_profile.py
"""
import os
import sys
import base64
import logging
import requests
from pathlib import Path
from requests_oauthlib import OAuth1
from dotenv import load_dotenv

load_dotenv("/var/www/neuralops/.env")

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.twitter_client import get_api_v1, is_configured

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

IMAGE_GEN_URL = "http://127.0.0.1:8098/generador/generate"

PROFILE = {
    "name": "Adrián Moreno",
    "description": (
        "AI/ML Engineer · LLMs · Python · FastAPI · "
        "Construyo sistemas inteligentes reales — demos interactivos en 👇"
    ),
    "location": "España",
    "url": "https://adrianmoreno-dev.com",
}

BANNER_PROMPT = (
    "futuristic dark blue tech background, glowing neural network nodes and data streams, "
    "AI machine learning visualization, deep navy #0a192f color scheme, "
    "cyan accent lines #64ffda, abstract circuit patterns, professional portfolio banner, "
    "widescreen 1500x500, no text, no people, cinematic lighting"
)


def generate_banner(output_path: str) -> bool:
    logger.info("Generando banner con FLUX.1-schnell...")
    try:
        resp = requests.post(IMAGE_GEN_URL, json={
            "prompt": BANNER_PROMPT,
            "style": "ninguno",
            "width": 1440,
            "height": 500,
        }, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        img_bytes = base64.b64decode(data["image_b64"])
        with open(output_path, "wb") as f:
            f.write(img_bytes)
        logger.info(f"Banner guardado en {output_path} ({data.get('elapsed_seconds', '?')}s)")
        return True
    except Exception as e:
        logger.error(f"Error generando banner: {e}")
        return False


def update_profile(api) -> bool:
    logger.info("Actualizando información del perfil...")
    try:
        api.update_profile(
            name=PROFILE["name"],
            url=PROFILE["url"],
            location=PROFILE["location"],
            description=PROFILE["description"],
        )
        logger.info("Perfil actualizado correctamente")
        logger.info(f"  Nombre: {PROFILE['name']}")
        logger.info(f"  Bio: {PROFILE['description']}")
        logger.info(f"  Ubicación: {PROFILE['location']}")
        logger.info(f"  Web: {PROFILE['url']}")
        return True
    except Exception as e:
        logger.error(f"Error actualizando perfil: {e}")
        return False


def _get_oauth1() -> OAuth1:
    return OAuth1(
        os.getenv("TWITTER_API_KEY"),
        os.getenv("TWITTER_API_SECRET"),
        os.getenv("TWITTER_ACCESS_TOKEN"),
        os.getenv("TWITTER_ACCESS_SECRET"),
    )


def upload_banner(api, image_path: str) -> bool:
    logger.info(f"Subiendo banner desde {image_path}...")
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
        banner_b64 = base64.b64encode(image_data).decode("utf-8")

        # Usar requests directamente con OAuth1 — tweepy v1 trata strings como rutas
        auth = _get_oauth1()
        resp = requests.post(
            "https://api.twitter.com/1.1/account/update_profile_banner.json",
            data={"banner": banner_b64},
            auth=auth,
            timeout=60,
        )
        if resp.status_code in (200, 201, 204):
            logger.info("Banner subido correctamente")
            return True
        else:
            logger.error(f"Error HTTP {resp.status_code}: {resp.text[:300]}")
            return False
    except Exception as e:
        logger.error(f"Error subiendo banner: {e}")
        return False


def main():
    if not is_configured():
        logger.error(
            "Credenciales Twitter no configuradas. "
            "Añade TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, "
            "TWITTER_ACCESS_SECRET en /var/www/neuralops/.env"
        )
        sys.exit(1)

    api = get_api_v1()

    # Verificar autenticación
    try:
        me = api.verify_credentials()
        logger.info(f"Autenticado como @{me.screen_name} (ID: {me.id})")
    except Exception as e:
        logger.error(f"Error de autenticación: {e}")
        sys.exit(1)

    results = {}

    # 1. Actualizar información del perfil
    results["profile"] = update_profile(api)

    # 2. Generar y subir banner
    banner_path = Path("/var/www/neuralops/assets/twitter_banner.png")
    banner_path.parent.mkdir(exist_ok=True)

    if generate_banner(str(banner_path)):
        results["banner"] = upload_banner(api, str(banner_path))
    else:
        logger.warning("No se pudo generar el banner — omitiendo subida")
        results["banner"] = False

    # Resumen
    print("\n" + "="*50)
    print("RESUMEN DE CONFIGURACIÓN TWITTER")
    print("="*50)
    for k, v in results.items():
        status = "OK" if v else "FALLIDO"
        print(f"  {k:20s}: {status}")

    if all(results.values()):
        print("\nPerfil de Twitter configurado completamente.")
    else:
        print("\nAlgunos pasos fallaron — revisa los logs.")


if __name__ == "__main__":
    main()
