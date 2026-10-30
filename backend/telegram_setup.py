"""Trova il codice della chat Telegram a cui il cruscotto manderà le notifiche.

Prima: crea il bot con @BotFather, metti TELEGRAM_BOT_TOKEN in backend/.env e scrivi
un messaggio qualsiasi al bot dal tuo Telegram. Poi sulla VPS:
    cd /var/www/trading/backend && venv/bin/python telegram_setup.py
"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not token:
    sys.exit("Manca TELEGRAM_BOT_TOKEN in backend/.env")

r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15).json()
if not r.get("ok"):
    sys.exit(f"Telegram ha rifiutato il token: {r.get('description')}")
chats = {}
for update in r.get("result", []):
    chat = (update.get("message") or update.get("edited_message") or {}).get("chat")
    if chat:
        chats[chat["id"]] = chat.get("first_name") or chat.get("title") or ""
if not chats:
    sys.exit("Nessun messaggio trovato: scrivi qualcosa al tuo bot su Telegram e rilancia lo script.")
for chat_id, name in chats.items():
    print(f"TELEGRAM_CHAT_ID={chat_id}    ({name})")
print("Copia la riga qui sopra in backend/.env e riavvia: systemctl restart trading")
