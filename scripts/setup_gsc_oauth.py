"""
Autenticación OAuth one-time para Google Search Console API.
Ejecutar UNA VEZ desde el servidor con acceso a navegador, o localmente.

Uso:
  python scripts/setup_gsc_oauth.py

Prerequisito: subir credentials.json a /var/www/neuralops/credentials.json
"""
import json
import os
import webbrowser
from urllib.parse import urlencode, urlparse, parse_qs
import httpx

CREDS_PATH = "/var/www/neuralops/credentials.json"
TOKEN_PATH = "/var/www/neuralops/.gsc_token.json"
SCOPES = "https://www.googleapis.com/auth/webmasters.readonly"

if not os.path.exists(CREDS_PATH):
    print("ERROR: No se encontró credentials.json")
    print("Pasos:")
    print("  1. console.cloud.google.com → tu proyecto")
    print("  2. APIs y servicios → Credenciales → Crear → OAuth 2.0 → Aplicación de escritorio")
    print("  3. Descargar JSON → subir como /var/www/neuralops/credentials.json")
    exit(1)

with open(CREDS_PATH) as f:
    creds = json.load(f)

client_id = creds["installed"]["client_id"]
client_secret = creds["installed"]["client_secret"]
redirect_uri = "urn:ietf:wg:oauth:2.0:oob"

# Step 1: Generate auth URL
params = {
    "client_id": client_id,
    "redirect_uri": redirect_uri,
    "response_type": "code",
    "scope": SCOPES,
    "access_type": "offline",
}
auth_url = f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"

print("\n=== Google Search Console OAuth Setup ===\n")
print("Abre esta URL en tu navegador e inicia sesión con tu cuenta de Google:")
print(f"\n{auth_url}\n")
print("Después de autorizar, copia el código que aparece y pégalo aquí.")

code = input("Código de autorización: ").strip()

# Step 2: Exchange code for token
with httpx.Client() as client:
    resp = client.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )

if resp.status_code != 200:
    print(f"Error obteniendo token: {resp.text}")
    exit(1)

token = resp.json()
token["client_id"] = client_id
token["client_secret"] = client_secret

with open(TOKEN_PATH, "w") as f:
    json.dump(token, f, indent=2)
os.chmod(TOKEN_PATH, 0o600)

print(f"\n✅ Token guardado en {TOKEN_PATH}")
print("El SEOMonitor ya puede conectarse a Google Search Console.")
print("\nNota: el token expira en 1h pero se renueva automáticamente con el refresh_token.")
