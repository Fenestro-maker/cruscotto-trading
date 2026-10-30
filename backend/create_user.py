"""Crea o aggiorna l'utente che può accedere al cruscotto (non c'è registrazione dal sito).

Uso sulla VPS:  cd /var/www/trading/backend && venv/bin/python create_user.py tua@email.it
La password viene chiesta a terminale e non compare nella cronologia.
"""
import asyncio
import getpass
import sys

import server


async def main(email: str) -> None:
    password = getpass.getpass("Password (almeno 10 caratteri): ")
    if len(password) < 10:
        sys.exit("Password troppo corta")
    if password != getpass.getpass("Ripeti la password: "):
        sys.exit("Le password non coincidono")
    await server.db.users.update_one(
        {"email": email.strip().lower()},
        {"$set": {"email": email.strip().lower(), "password": server.hash_password(password)}},
        upsert=True,
    )
    print(f"Utente {email} pronto")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Uso: venv/bin/python create_user.py tua@email.it")
    asyncio.run(main(sys.argv[1]))
