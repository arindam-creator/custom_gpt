import os
from dotenv import load_dotenv

load_dotenv()

# --- Server Config ---
PORT = int(os.getenv("PORT", 8005))
ENVIRONMENT = os.getenv("ENVIRONMENT", "local")  # 'local' or 'production'

# --- Django Backend Configuration ---
# CRITICAL: On Render, this cannot be localhost. It must be the public URL of your Django API.
DJANGO_BASE_URL = os.getenv("DJANGO_BASE_URL", "https://salesapi.gravityer.com/api/v1")
DJANGO_LOGIN_URL = DJANGO_BASE_URL.rstrip("/") + "/token/obtain/"

# --- Security ---
# This key acts as the bridge's private key to sign OAuth codes.
MCP_SECRET_KEY = os.getenv("MCP_SECRET_KEY")
if not MCP_SECRET_KEY:
    raise ValueError("FATAL: MCP_SECRET_KEY environment variable is not set!")

# Optional: Fallback token for local testing/single-user mode
DJANGO_AUTH_TOKEN = os.getenv("DJANGO_AUTH_TOKEN")