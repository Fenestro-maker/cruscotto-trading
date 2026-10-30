"""Backend per le preview nelle sessioni Claude Code nel cloud: NON usare in produzione.

- MongoDB simulato in memoria (mongomock-motor): dati persi a ogni riavvio.
- OKX simulato: candele generate a caso (da qui www.okx.com non è raggiungibile).
- Claude simulato se manca ANTHROPIC_API_KEY: piano di prova fisso.
Utente di prova: demo@trading.local / demo12345
"""
import os
import random
import sys
import time
from pathlib import Path

import motor.motor_asyncio
import uvicorn
from mongomock_motor import AsyncMongoMockClient

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
os.environ.setdefault("SECRET_KEY", "preview-non-segreto")
os.environ.setdefault("DB_NAME", "trading_preview")
motor.motor_asyncio.AsyncIOMotorClient = AsyncMongoMockClient
sys.path.insert(0, str(BACKEND_DIR))

import server  # noqa: E402

BAR_MS = {"1Wutc": 7 * 86400_000, "1Dutc": 86400_000, "4H": 4 * 3600_000, "1H": 3600_000, "15m": 900_000}
_series: dict[str, list] = {}


def fake_series(bar: str, start_price: float = 2340.0) -> list:
    """Passeggiata casuale che termina vicino a start_price, in formato OKX (più recente prima)"""
    if bar in _series:
        return _series[bar]
    rnd = random.Random(bar)
    step = BAR_MS[bar]
    now = int(time.time() * 1000) // step * step
    vol = {"1Wutc": 0.06, "1Dutc": 0.025, "4H": 0.01, "1H": 0.005, "15m": 0.0025}[bar]
    price = start_price
    rows = []
    for i in range(300):
        c = price
        o = c / (1 + rnd.gauss(0, vol))
        h = max(o, c) * (1 + abs(rnd.gauss(0, vol / 2)))
        low = min(o, c) * (1 - abs(rnd.gauss(0, vol / 2)))
        rows.append([str(now - i * step), f"{o:.2f}", f"{h:.2f}", f"{low:.2f}", f"{c:.2f}",
                     str(rnd.randint(1000, 9000)), "0", "0", "0" if i == 0 else "1"])
        price = o
    _series[bar] = rows
    return rows


async def fake_okx_public(path: str, params: dict) -> list:
    inst = params.get("instId", "ETH-USDC-SWAP")
    if path == "/api/v5/market/candles":
        return fake_series(params["bar"])[: int(params.get("limit", 100))]
    if path == "/api/v5/market/ticker":
        last = float(fake_series("15m")[0][4]) + random.uniform(-2, 2)
        return [{"last": f"{last:.2f}", "bidPx": f"{last - 0.05:.2f}", "askPx": f"{last + 0.05:.2f}",
                 "open24h": "2301.10", "high24h": "2392.40", "low24h": "2288.70", "ts": str(int(time.time() * 1000))}]
    if path == "/api/v5/public/instruments":
        return [{"instId": inst, "ctVal": "0.1", "ctValCcy": inst.split("-")[0], "lotSz": "0.01",
                 "minSz": "0.01", "tickSz": "0.01", "settleCcy": "USDC"}]
    return []


async def fake_ask_claude(system: str, user_text: str):
    last = float(fake_series("15m")[0][4])
    entry = round(last - 8, 1)
    return {
        "decision": "trade",
        "summary": "[PIANO DI PROVA, non generato da Claude] Struttura 1h rialzista dopo la rottura del range: si aspetta il retest della resistenza rotta.",
        "weekly": "Correzione dentro il trend rialzista partito dall'estate.",
        "daily": f"Bias rialzista finché il prezzo resta sopra {entry - 60:.0f}.",
        "structure": f"Range 1h rotto al rialzo, livello chiave {entry:.0f} da ritestare.",
        "timing": "Ingresso solo con candela di rifiuto sul 15m nella zona.",
        "key_levels": [
            {"price": entry + 55, "kind": "resistenza", "note": "massimo del range precedente"},
            {"price": entry, "kind": "supporto", "note": "resistenza rotta"},
            {"price": entry - 60, "kind": "supporto", "note": "minimo giornaliero"},
        ],
        "setup": "retest_rottura", "side": "long",
        "entry": entry, "stop": entry - 22, "tp1": entry + 32, "target": entry + 47,
        "cancel_above": entry + 54, "cancel_below": entry - 23, "expiry_hours": 5,
        "cancel_conditions": [f"Chiusura 1h sopra {entry + 54:.0f} senza retest", "Nessun riempimento entro fine sessione"],
        "invalidation": f"Chiusura 1h sotto {entry - 22:.0f}.",
        "bias": "Rialzista sopra la resistenza rotta.",
        "risks": ["Dati di prova generati a caso"],
        "confidence": "media",
    }, {"model": "simulato", "input_tokens": 0, "output_tokens": 0}


server.okx_public = fake_okx_public
if not server.ANTHROPIC_API_KEY:
    server.ask_claude = fake_ask_claude
    server.ANTHROPIC_API_KEY = "simulato"  # abilita il pulsante "Ragiona ora" nell'interfaccia


@server.app.on_event("startup")
async def seed_demo_user():
    if not await server.db.users.find_one({"email": "demo@trading.local"}):
        await server.db.users.insert_one({"email": "demo@trading.local", "password": server.hash_password("demo12345")})


if __name__ == "__main__":
    uvicorn.run(server.app, host="127.0.0.1", port=8003)
