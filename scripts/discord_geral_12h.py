"""NOCRY: aviso geral de atualização de personagens a cada 12 horas.

Executar somente por agendador (Render Cron Job), não pelo servidor Flask.
Requer DISCORD_GERAL_WEBHOOK apontando especificamente para o canal #geral.
"""
import json
import os
import sys
from urllib.request import Request, urlopen

WEBHOOK = os.environ.get("DISCORD_GERAL_WEBHOOK", "").strip()
PORTAL = os.environ.get("NOCRY_PORTAL_URL", "https://nocry-portal-teste.onrender.com").rstrip("/")
if not WEBHOOK.startswith(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
    sys.exit("DISCORD_GERAL_WEBHOOK ausente ou inválido. Nenhum aviso enviado.")

payload = {
    "username": "NOCRY · Lembretes",
    "content": "@everyone ⚔️ **NOCRY — HORA DE ATUALIZAR OS PERSONAGENS!**",
    "allowed_mentions": {"parse": ["everyone"]},
    "embeds": [{
        "title": "🔥 ATUALIZAÇÃO A CADA 12 HORAS",
        "description": (
            "Guerreiros do NOCRY, N0CRY e NOWCRY!\n\n"
            "Entrem no painel e atualizem o **nível** e a **experiência (%)** "
            "dos seus personagens para manter o ranking em dia.\n\n"
            f"🔗 **Painel:** {PORTAL}"
        ),
        "color": 16742438,
        "footer": {"text": "NOCRY • Priston Tale | Aviso automático a cada 12 horas"}
    }]
}
req = Request(WEBHOOK, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
              headers={"Content-Type": "application/json", "User-Agent": "NOCRY-Reminder/1.0"},
              method="POST")
with urlopen(req, timeout=15) as response:
    if response.status not in (200, 204):
        raise RuntimeError(f"Discord retornou HTTP {response.status}")
print("Aviso enviado ao canal geral.")
