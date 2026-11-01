"""Cruscotto di trading a sessione: backend FastAPI.

Dati di mercato dalle API pubbliche di OKX, analisi top-down con Claude,
piano della sessione con le regole fisse di rischio, diario delle sessioni.
Nessun ordine viene inviato a OKX.
"""
import asyncio
import base64
import csv
import hashlib
import hmac
import html
import io
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import anthropic
import bcrypt
import httpx
import jwt
from anthropic import AsyncAnthropic
from bson import ObjectId
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, field_validator

load_dotenv(Path(__file__).parent / ".env")

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "trading")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "claude-opus-5-5")
AI_EFFORT = os.environ.get("AI_EFFORT", "high")
# Classifica del mattino: lettura veloce di molti strumenti con un modello più economico
SCAN_AI_MODEL = os.environ.get("SCAN_AI_MODEL", "claude-sonnet-5-5")
SCAN_AI_EFFORT = os.environ.get("SCAN_AI_EFFORT", "medium")
OKX_BASE_URL = os.environ.get("OKX_BASE_URL", "https://www.okx.com")  # OKX Europa (my.okx.com): https://eea.okx.com
# Chiave API OKX con il solo permesso "Leggi": serve a mostrare conto, posizioni e ordini
OKX_API_KEY = os.environ.get("OKX_API_KEY", "")
OKX_API_SECRET = os.environ.get("OKX_API_SECRET", "")
OKX_API_PASSPHRASE = os.environ.get("OKX_API_PASSPHRASE", "")
# Notifiche Telegram: token del bot (da @BotFather) e chat a cui scrivere (backend/telegram_setup.py)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
MONITOR_INTERVAL_S = 20

if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY mancante in backend/.env")

TZ = ZoneInfo("Europe/Rome")
TOKEN_DAYS = 7

# Regole fisse: non modificabili dalle impostazioni
MAX_RISK_PCT = 1.0
MAX_LEVERAGE = 3.0
MIN_RR = 2.0
DAILY_MAX_LOSSES_IN_ROW = 2
DAILY_MAX_LOSS_R = -2.0

SETUPS = {
    "retest_rottura": "Retest della rottura",
    "pullback_trend": "Pullback nel trend",
    "sweep_liquidita": "Presa di liquidità (sweep)",
    "rottura_range": "Rottura del range",
    "nessuno": "Nessun trade",
}

DEFAULT_SETTINGS = {
    "capital": 2000.0,
    "risk_pct": 1.0,
    "leverage": 2.0,
    "instruments": ["ETH-USDC-SWAP", "BTC-USDC-SWAP", "SOL-USDC-SWAP"],
    "profit_alert_usd": 40.0,
    "review_every_hours": 24.0,
    "scan_enabled": True,
    "scan_time": "08:30",
}

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
app = FastAPI(title="Cruscotto di trading")
api = APIRouter(prefix="/api")
security = HTTPBearer()


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Errori di validazione in una riga leggibile (campo: problema)"""
    parts = [f"{e['loc'][-1]}: {e['msg']}" for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Dati non validi · " + " · ".join(parts)})


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Data non valida: {value}")
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def oid(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except Exception:
        raise HTTPException(status_code=404, detail="Elemento non trovato")


# ---------- Autenticazione ----------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Sessione scaduta: accedi di nuovo")
    user = await db.users.find_one({"email": payload.get("sub")})
    if not user:
        raise HTTPException(status_code=401, detail="Utente non trovato")
    return user


# Tentativi di accesso falliti per indirizzo IP (in memoria)
FAILED_LOGINS: dict[str, list[float]] = {}
LOGIN_WINDOW_S = 15 * 60
LOGIN_MAX_FAILS = 10


class LoginIn(BaseModel):
    email: str
    password: str


@api.post("/auth/login")
async def login(data: LoginIn, request: Request):
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    recent = [t for t in FAILED_LOGINS.get(ip, []) if time.time() - t < LOGIN_WINDOW_S]
    if len(recent) >= LOGIN_MAX_FAILS:
        raise HTTPException(status_code=429, detail="Troppi tentativi: riprova tra 15 minuti")
    user = await db.users.find_one({"email": data.email.strip().lower()})
    if not user or not check_password(data.password, user["password"]):
        FAILED_LOGINS[ip] = recent + [time.time()]
        raise HTTPException(status_code=401, detail="Email o password errati")
    FAILED_LOGINS.pop(ip, None)
    token = jwt.encode(
        {"sub": user["email"], "exp": now_utc() + timedelta(days=TOKEN_DAYS)},
        SECRET_KEY, algorithm="HS256",
    )
    return {"access_token": token, "user": {"email": user["email"], "name": user.get("name", "")}}


@api.get("/auth/me")
async def me(user: dict = Depends(get_current_user)):
    return {"email": user["email"], "name": user.get("name", "")}


@api.get("/health")
async def health():
    return {"ok": True, "ai_configured": bool(ANTHROPIC_API_KEY), "okx_account": okx_account_configured()}


# ---------- Impostazioni ----------

class SettingsIn(BaseModel):
    capital: float = Field(gt=0)
    risk_pct: float = Field(gt=0, le=MAX_RISK_PCT)
    leverage: float = Field(ge=1, le=MAX_LEVERAGE)
    instruments: list[str] = Field(min_length=1, max_length=500)
    profit_alert_usd: float = Field(default=40.0, gt=0)
    review_every_hours: float = Field(default=24.0, ge=0, le=168)  # 0 = ricontrollo automatico spento
    scan_enabled: bool = True
    scan_time: str = Field(default="08:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # ora italiana

    @field_validator("instruments")
    @classmethod
    def clean_instruments(cls, v: list[str]) -> list[str]:
        out = []
        for inst in v:
            inst = inst.strip().upper()
            if inst and inst not in out:
                out.append(inst)
        if not out:
            raise ValueError("Serve almeno uno strumento")
        return out


async def load_settings() -> dict:
    doc = await db.settings.find_one({"_id": "main"}) or {}
    return {k: doc.get(k, v) for k, v in DEFAULT_SETTINGS.items()}


@api.get("/settings")
async def get_settings(user: dict = Depends(get_current_user)):
    return {**await load_settings(), "rules": rules_info(), "ai_configured": bool(ANTHROPIC_API_KEY), "ai_model": AI_MODEL,
            "okx_base_url": OKX_BASE_URL, "okx_account": okx_account_configured(),
            "telegram_configured": telegram_configured()}


@api.put("/settings")
async def put_settings(data: SettingsIn, user: dict = Depends(get_current_user)):
    # Un elenco per tipo di strumento (poche richieste anche con centinaia di codici)
    types = sorted({inst_type_of(i) for i in data.instruments})
    known = dict(zip(types, await asyncio.gather(*(okx_instrument_ids(t) for t in types))))
    missing = [i for i in data.instruments if i not in known[inst_type_of(i)]]
    if missing:
        raise HTTPException(status_code=400, detail=f"Su OKX non esistono: {', '.join(missing)}. Controlla i codici (su OKX Europa gli X-Perp sono del tipo BTC-USD_UM_XPERP-310404).")
    await db.settings.update_one({"_id": "main"}, {"$set": data.model_dump()}, upsert=True)
    return await get_settings(user)


def rules_info() -> dict:
    return {
        "max_risk_pct": MAX_RISK_PCT,
        "max_leverage": MAX_LEVERAGE,
        "min_rr": MIN_RR,
        "daily_max_losses_in_row": DAILY_MAX_LOSSES_IN_ROW,
        "daily_max_loss_r": DAILY_MAX_LOSS_R,
    }


# ---------- OKX: dati pubblici ----------

# Timeframe dell'app → parametro "bar" di OKX (giornaliero e settimanale allineati a UTC)
BARS = {"1W": "1Wutc", "1D": "1Dutc", "4H": "4H", "1H": "1H", "15m": "15m"}

_okx_http: Optional[httpx.AsyncClient] = None


def okx_http() -> httpx.AsyncClient:
    global _okx_http
    if _okx_http is None:
        # Sempre IPv4: la chiave API OKX è limitata all'indirizzo IPv4 della VPS
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1)
        _okx_http = httpx.AsyncClient(base_url=OKX_BASE_URL, timeout=15, transport=transport)
    return _okx_http


async def okx_public(path: str, params: dict) -> list:
    """GET su un endpoint pubblico di OKX; restituisce il campo data"""
    try:
        r = await okx_http().get(path, params=params)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="OKX non raggiungibile: riprova tra poco")
    try:
        body = r.json()
    except ValueError:
        raise HTTPException(status_code=502, detail=f"Risposta di OKX non leggibile (HTTP {r.status_code})")
    if body.get("code") != "0":
        raise HTTPException(status_code=502, detail=f"OKX: {body.get('msg') or 'errore'} (codice {body.get('code')})")
    return body.get("data", [])


def inst_type_of(inst: str) -> str:
    """Tipo OKX dal codice: perpetual classici (-SWAP), futures e X-Perp di OKX Europa (scadenza -AAMMGG), spot"""
    if inst.endswith("-SWAP"):
        return "SWAP"
    if re.search(r"-\d{6}$", inst):
        return "FUTURES"
    return "SPOT"


async def fetch_candles(inst: str, tf: str, limit: int) -> list[dict]:
    """Candele in ordine cronologico; l'ultima può essere ancora in formazione"""
    if tf not in BARS:
        raise HTTPException(status_code=400, detail=f"Timeframe non valido: {tf}")
    rows = await okx_public("/api/v5/market/candles", {"instId": inst, "bar": BARS[tf], "limit": str(min(limit, 300))})
    candles = [
        {"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]),
         "v": float(r[5]), "confirmed": r[8] == "1"}
        for r in rows
    ]
    candles.sort(key=lambda c: c["t"])
    return candles


async def fetch_ticker(inst: str) -> dict:
    data = await okx_public("/api/v5/market/ticker", {"instId": inst})
    if not data:
        raise HTTPException(status_code=404, detail=f"Strumento non trovato su OKX: {inst}")
    t = data[0]
    return {
        "inst": inst,
        "last": float(t["last"]),
        "bid": float(t["bidPx"]) if t.get("bidPx") else None,
        "ask": float(t["askPx"]) if t.get("askPx") else None,
        "open24h": float(t["open24h"]),
        "high24h": float(t["high24h"]),
        "low24h": float(t["low24h"]),
        "ts": int(t["ts"]),
    }


@api.get("/market/ticker")
async def market_ticker(inst: str, user: dict = Depends(get_current_user)):
    return await fetch_ticker(inst)


@api.get("/market/candles")
async def market_candles(inst: str, tf: str = "1H", limit: int = 300, user: dict = Depends(get_current_user)):
    return await fetch_candles(inst, tf, limit)


@api.get("/market/instrument")
async def market_instrument(inst: str, user: dict = Depends(get_current_user)):
    """Dati del contratto: servono per tradurre la quantità in contratti OKX"""
    inst_type = inst_type_of(inst)
    data = await okx_public("/api/v5/public/instruments", {"instType": inst_type, "instId": inst})
    if not data:
        raise HTTPException(status_code=404, detail=f"Strumento non trovato su OKX: {inst}")
    d = data[0]
    num = lambda k: float(d[k]) if d.get(k) else None  # noqa: E731
    return {
        "inst": inst, "type": inst_type,
        "ct_val": num("ctVal"), "ct_val_ccy": d.get("ctValCcy") or d.get("baseCcy"),
        "lot_sz": num("lotSz"), "min_sz": num("minSz"), "tick_sz": num("tickSz"),
        "settle_ccy": d.get("settleCcy") or d.get("quoteCcy"),
    }


_instrument_cache: dict[str, tuple[float, list[dict]]] = {}


async def okx_instruments(inst_type: str) -> list[dict]:
    """Tutti gli strumenti OKX di un tipo (SWAP, FUTURES, SPOT), nell'ordine di OKX; tenuti 10 minuti in memoria"""
    cached = _instrument_cache.get(inst_type)
    if cached and time.time() - cached[0] < 600:
        return cached[1]
    rows = await okx_public("/api/v5/public/instruments", {"instType": inst_type})
    _instrument_cache[inst_type] = (time.time(), rows)
    return rows


async def okx_instrument_ids(inst_type: str) -> list[str]:
    return [r["instId"] for r in await okx_instruments(inst_type)]


@api.get("/market/xperps")
async def market_xperps(user: dict = Depends(get_current_user)):
    """Tutti gli X-Perp di OKX Europa (FUTURES con codice ..._XPERP-AAMMGG)"""
    return [i for i in await okx_instrument_ids("FUTURES") if "_XPERP-" in i]


# ---------- OKX: conto in sola lettura ----------

def okx_account_configured() -> bool:
    return bool(OKX_API_KEY and OKX_API_SECRET and OKX_API_PASSPHRASE)


async def okx_private_get(path: str, params: Optional[dict] = None) -> list:
    """GET firmato su OKX. Solo lettura: questo backend non fa mai richieste POST a OKX."""
    if not okx_account_configured():
        raise HTTPException(status_code=503, detail="Chiave API OKX non configurata in backend/.env")
    query = urlencode({k: v for k, v in (params or {}).items() if v is not None}, safe=",")
    request_path = path + (f"?{query}" if query else "")
    now = now_utc()
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    signature = base64.b64encode(
        hmac.new(OKX_API_SECRET.encode(), f"{timestamp}GET{request_path}".encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "OK-ACCESS-KEY": OKX_API_KEY,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": OKX_API_PASSPHRASE,
    }
    try:
        r = await okx_http().get(request_path, headers=headers)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="OKX non raggiungibile: riprova tra poco")
    try:
        body = r.json()
    except ValueError:
        raise HTTPException(status_code=502, detail=f"Risposta di OKX non leggibile (HTTP {r.status_code})")
    code = body.get("code")
    if code != "0":
        hints = {
            "50110": "l'IP della VPS non è tra gli IP attendibili della chiave",
            "50111": "chiave API non valida",
            "50113": "firma non valida: controlla la Secret key",
            "50105": "passphrase sbagliata",
        }
        hint = hints.get(code, body.get("msg") or "errore")
        if code == "50110" and body.get("msg"):
            hint += f" · messaggio di OKX: {body['msg']}"  # contiene l'IP visto da OKX
        raise HTTPException(status_code=502, detail=f"OKX (conto): {hint} (codice {code})")
    return body.get("data", [])


def fnum(value) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def ms_iso(value) -> Optional[str]:
    return iso(datetime.fromtimestamp(int(value) / 1000, timezone.utc)) if value else None


@api.get("/account")
async def account(user: dict = Depends(get_current_user)):
    """Conto OKX in sola lettura: saldo, posizioni, ordini in attesa, stop/TP, ultime posizioni chiuse"""
    if not okx_account_configured():
        return {"configured": False}
    balance, positions, orders, algos, history = await asyncio.gather(
        okx_private_get("/api/v5/account/balance"),
        okx_private_get("/api/v5/account/positions"),
        okx_private_get("/api/v5/trade/orders-pending"),
        okx_private_get("/api/v5/trade/orders-algo-pending", {"ordType": "conditional,oco"}),
        okx_private_get("/api/v5/account/positions-history", {"limit": "20"}),
    )
    bal = balance[0] if balance else {}
    return {
        "configured": True,
        "updated_at": iso(now_utc()),
        "total_eq": fnum(bal.get("totalEq")),
        "currencies": [
            {"ccy": d["ccy"], "eq": fnum(d.get("eq")), "avail": fnum(d.get("availBal"))}
            for d in bal.get("details", []) if (fnum(d.get("eq")) or 0) > 0
        ],
        "positions": [
            {
                "inst": p["instId"], "pos": fnum(p.get("pos")), "pos_side": p.get("posSide"),
                "avg_px": fnum(p.get("avgPx")), "mark_px": fnum(p.get("markPx")), "liq_px": fnum(p.get("liqPx")),
                "lever": fnum(p.get("lever")), "mgn_mode": p.get("mgnMode"), "upl": fnum(p.get("upl")),
                "margin": fnum(p.get("margin") or p.get("imr")), "opened_at": ms_iso(p.get("cTime")),
            }
            for p in positions if fnum(p.get("pos"))
        ],
        "orders": [
            {
                "inst": o["instId"], "side": o.get("side"), "pos_side": o.get("posSide"), "ord_type": o.get("ordType"),
                "px": fnum(o.get("px")), "sz": fnum(o.get("sz")), "filled": fnum(o.get("accFillSz")),
                "tp": fnum(o.get("tpTriggerPx")) or next((fnum(a.get("tpTriggerPx")) for a in o.get("attachAlgoOrds") or [] if a.get("tpTriggerPx")), None),
                "sl": fnum(o.get("slTriggerPx")) or next((fnum(a.get("slTriggerPx")) for a in o.get("attachAlgoOrds") or [] if a.get("slTriggerPx")), None),
                "created_at": ms_iso(o.get("cTime")),
            }
            for o in orders
        ],
        "algo_orders": [
            {
                "inst": a["instId"], "side": a.get("side"), "ord_type": a.get("ordType"), "sz": fnum(a.get("sz")),
                "tp": fnum(a.get("tpTriggerPx")), "sl": fnum(a.get("slTriggerPx")),
                "close_position": a.get("closeFraction") == "1",
            }
            for a in algos
        ],
        "history": [
            {
                "inst": h["instId"], "direction": h.get("direction"), "open_px": fnum(h.get("openAvgPx")),
                "close_px": fnum(h.get("closeAvgPx")), "size": fnum(h.get("closeTotalPos") or h.get("openMaxPos")),
                "pnl": fnum(h.get("realizedPnl")), "fee": fnum(h.get("fee")), "close_type": h.get("type"),
                "opened_at": ms_iso(h.get("cTime")), "closed_at": ms_iso(h.get("uTime")),
            }
            for h in history
        ],
    }


# ---------- OKX: funding rate e open interest ----------

OI_SNAPSHOT_EVERY_S = 15 * 60  # fotografia dell'open interest (OKX Europa non ha lo storico)
_last_oi_snapshot = 0.0


async def fetch_funding(inst: str) -> Optional[dict]:
    """Funding attuale e degli ultimi 30 periodi (solo perpetual e X-Perp)"""
    if inst_type_of(inst) == "SPOT":
        return None
    current, history = await asyncio.gather(
        okx_public("/api/v5/public/funding-rate", {"instId": inst}),
        okx_public("/api/v5/public/funding-rate-history", {"instId": inst, "limit": "30"}),
    )
    if not current:
        return None
    c = current[0]
    rate = fnum(c.get("fundingRate"))
    funding_time, next_time = fnum(c.get("fundingTime")), fnum(c.get("nextFundingTime"))
    interval_h = round((next_time - funding_time) / 3_600_000, 2) if funding_time and next_time and next_time > funding_time else None
    past = [fnum(h.get("realizedRate") or h.get("fundingRate")) for h in history]
    past = [x for x in past if x is not None]
    return {
        "rate": rate,
        "next_rate": fnum(c.get("nextFundingRate")),
        "interval_h": interval_h,
        "next_time": ms_iso(c.get("fundingTime")),
        "annual_pct": round(rate * 100 * (24 / interval_h) * 365, 1) if rate is not None and interval_h else None,
        "avg_30": sum(past) / len(past) if past else None,
        "min_30": min(past) if past else None,
        "max_30": max(past) if past else None,
        "positive_30": sum(1 for x in past if x > 0),
        "count_30": len(past),
    }


async def fetch_open_interest(inst: str) -> Optional[dict]:
    if inst_type_of(inst) == "SPOT":
        return None
    rows = await okx_public("/api/v5/public/open-interest", {"instType": inst_type_of(inst), "instId": inst})
    if not rows:
        return None
    r = rows[0]
    return {"oi": fnum(r.get("oi")), "oi_ccy": fnum(r.get("oiCcy")), "oi_usd": fnum(r.get("oiUsd"))}


async def oi_change(inst: str, oi_ccy: Optional[float], price: float, hours: int) -> Optional[dict]:
    """Variazione dell'open interest e del prezzo rispetto alla fotografia di circa N ore fa"""
    if oi_ccy is None:
        return None
    since = now_utc() - timedelta(hours=hours)
    snap = await db.oi_snapshots.find_one(
        {"inst": inst, "ts": {"$lte": since, "$gte": since - timedelta(hours=max(1, hours / 4))}}, sort=[("ts", -1)]
    )
    if not snap or not snap.get("oi_ccy"):
        return None
    return {
        "hours": hours,
        "oi_pct": round((oi_ccy - snap["oi_ccy"]) / snap["oi_ccy"] * 100, 2),
        "price_pct": round((price - snap["price"]) / snap["price"] * 100, 2) if snap.get("price") else None,
    }


async def derivatives_info(inst: str, price: Optional[float] = None) -> dict:
    """Funding e open interest; ogni parte è facoltativa (se OKX non risponde si va avanti senza)"""
    funding, oi = await asyncio.gather(fetch_funding(inst), fetch_open_interest(inst), return_exceptions=True)
    funding = None if isinstance(funding, Exception) else funding
    oi = None if isinstance(oi, Exception) else oi
    if price is None:
        try:
            price = (await fetch_ticker(inst))["last"]
        except HTTPException:
            price = None
    changes = []
    if oi and price:
        for h in (4, 24):
            ch = await oi_change(inst, oi["oi_ccy"], price, h)
            if ch:
                changes.append(ch)
    return {"inst": inst, "funding": funding, "open_interest": oi, "oi_changes": changes}


def derivatives_block(d: dict) -> str:
    """Funding e open interest in testo per Claude"""
    lines = ["## Derivati (OKX)"]
    f = d.get("funding")
    if f and f["rate"] is not None:
        every = f" ogni {f['interval_h']:g} ore" if f["interval_h"] else ""
        lines.append(f"Funding attuale {f['rate'] * 100:+.4f}%{every}" + (f" (≈ {f['annual_pct']:+.1f}% annuo)" if f["annual_pct"] is not None else ""))
        if f["count_30"]:
            lines.append(f"Ultimi {f['count_30']} periodi: media {f['avg_30'] * 100:+.4f}%, minimo {f['min_30'] * 100:+.4f}%, "
                         f"massimo {f['max_30'] * 100:+.4f}%, positivi {f['positive_30']} su {f['count_30']}")
    else:
        lines.append("Funding: non disponibile")
    o = d.get("open_interest")
    if o and o["oi_ccy"] is not None:
        usd = f" (≈ {o['oi_usd']:,.0f} $)" if o.get("oi_usd") else ""
        lines.append(f"Open interest attuale: {o['oi_ccy']:,.2f} in valuta base{usd}")
        for ch in d.get("oi_changes", []):
            price = f", prezzo {ch['price_pct']:+.2f}%" if ch["price_pct"] is not None else ""
            lines.append(f"Ultime {ch['hours']} ore: open interest {ch['oi_pct']:+.2f}%{price}")
        if not d.get("oi_changes"):
            lines.append("Variazione dell'open interest: storico non ancora disponibile")
    else:
        lines.append("Open interest: non disponibile")
    return "\n".join(lines)


async def snapshot_open_interest() -> None:
    """Ogni 15 minuti salva open interest e prezzo degli strumenti seguiti (storico per le variazioni)"""
    global _last_oi_snapshot
    if time.time() - _last_oi_snapshot < OI_SNAPSHOT_EVERY_S:
        return
    _last_oi_snapshot = time.time()
    insts = set((await load_settings())["instruments"])
    insts |= {d["instrument"] async for d in db.sessions.find({"status": {"$in": ["attesa", "aperta"]}}, {"instrument": 1})}
    now = now_utc()
    # Due richieste per tipo di strumento, qualunque sia il numero di strumenti seguiti
    for inst_type in sorted({inst_type_of(i) for i in insts} - {"SPOT"}):
        try:
            ois, tickers = await asyncio.gather(
                okx_public("/api/v5/public/open-interest", {"instType": inst_type}),
                okx_public("/api/v5/market/tickers", {"instType": inst_type}),
            )
        except HTTPException:
            continue
        prices = {t["instId"]: fnum(t.get("last")) for t in tickers}
        docs = [
            {"inst": o["instId"], "ts": now, "oi_ccy": fnum(o.get("oiCcy")), "price": prices.get(o["instId"])}
            for o in ois
            if o["instId"] in insts and fnum(o.get("oiCcy")) and prices.get(o["instId"])
        ]
        if docs:
            await db.oi_snapshots.insert_many(docs)


@api.get("/market/derivatives")
async def market_derivatives(inst: str, user: dict = Depends(get_current_user)):
    return await derivatives_info(inst)


# ---------- Indicatori per l'analisi ----------

def sma(values: list[float], n: int) -> Optional[float]:
    return sum(values[-n:]) / n if len(values) >= n else None


def rsi(values: list[float], n: int = 14) -> Optional[float]:
    if len(values) <= n:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / n, losses / n
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
    if avg_l == 0:
        return 100.0
    return 100 - 100 / (1 + avg_g / avg_l)


def fmt_price(x: Optional[float]) -> str:
    if x is None:
        return "n/d"
    digits = 2 if abs(x) >= 100 else 4 if abs(x) >= 1 else 6
    return f"{x:.{digits}f}"


def candles_block(tf: str, candles: list[dict]) -> str:
    closes = [c["c"] for c in candles if c["confirmed"]]
    ind = (
        f"SMA20 {fmt_price(sma(closes, 20))} · SMA50 {fmt_price(sma(closes, 50))} · "
        f"SMA200 {fmt_price(sma(closes, 200))} · RSI14 {fmt_price(rsi(closes))}"
    )
    lines = [f"## {tf} ({len(candles)} candele, ora italiana)", f"Indicatori sulle candele chiuse: {ind}",
             "data,open,high,low,close,volume"]
    date_fmt = "%d/%m/%Y" if tf in ("1W", "1D") else "%d/%m %H:%M"
    for c in candles:
        when = datetime.fromtimestamp(c["t"] / 1000, TZ).strftime(date_fmt)
        flag = "" if c["confirmed"] else ",IN FORMAZIONE"
        lines.append(f"{when},{fmt_price(c['o'])},{fmt_price(c['h'])},{fmt_price(c['l'])},{fmt_price(c['c'])},{c['v']:.0f}{flag}")
    return "\n".join(lines)


# Numero di candele per timeframe passate a Claude
ANALYSIS_CANDLES = {"1W": 104, "1D": 200, "4H": 180, "1H": 168, "15m": 96}


# ---------- Analisi con Claude ----------

ANALYSIS_SYSTEM = f"""Sei l'analista di un trader discrezionale che opera a sessione su OKX (perpetual, margine isolato), solo price action: niente notizie.
Il tuo compito è leggere il mercato ADESSO e proporre il setup della sessione, oppure dire che oggi non si trada. "Nessun trade" è una risposta legittima e spesso giusta: non forzare un ingresso.

Metodo, sempre in quest'ordine (analisi top-down):
1. Settimanale: direzione di fondo (trend o correzione dentro un trend).
2. Giornaliero: bias e zone chiave (supporti, resistenze, livelli di Fibonacci).
3. 4h e 1h: struttura (massimi e minimi, range, rotture).
4. 15m: solo per il timing di ingresso. Mai entrare sul 15m contro la struttura dell'1h.

Setup ammessi (campo setup):
- retest_rottura: il prezzo rompe un livello, torna a testarlo dall'altra parte e tiene. Si entra sul retest, non sulla rottura.
- pullback_trend: massimi crescenti (o minimi decrescenti per lo short) sull'1h, ritorno su un supporto, candela di rifiuto sul 15m.
- sweep_liquidita: il prezzo buca un minimo (o massimo) evidente e rientra subito.
- rottura_range: chiusura 1h oltre il bordo del range e retest tenuto.
- nessuno: nessun trade oggi.

Regole fisse (non cambiano mai, il piano deve rispettarle):
- Rischio 1% del capitale per trade; la quantità la calcola il cruscotto dallo stop.
- Rapporto rischio/rendimento minimo {MIN_RR:g}:1 misurato tra ingresso, stop e target finale; sotto questa soglia non si entra.
- Stop sempre inserito con l'ordine, in un punto che invalida davvero l'idea (oltre la zona, non a caso).
- Gestione: metà a TP1 (prima della resistenza/supporto successivo), resto al target, stop a pareggio dopo TP1.
- Ingresso con ordine limit (maker): le commissioni decidono il risultato sui timeframe bassi. Indica un livello limit preciso, non "a mercato".
- Stop giornaliero dopo 2 perdite di fila o -2R.

Indicazioni sulla risposta:
- Scrivi in italiano semplice, frasi brevi, cita i livelli con i prezzi.
- Per un long: stop < ingresso < TP1 <= target. Per uno short: stop > ingresso > TP1 >= target.
- cancel_above / cancel_below: soglie oltre le quali l'ordine limit va annullato se non ancora eseguito (es. chiusura 1h oltre la resistenza per un long che aspetta il retest, oppure prezzo sotto lo stop prima del riempimento).
- expiry_hours: entro quante ore annullare l'ordine se non viene eseguito (di solito fino a fine sessione).
- Se decision è no_trade, metti side "none", setup "nessuno" e i livelli a null; spiega cosa aspetteresti per entrare.
- Oro, petrolio, indici e valute hanno orari di mercato: chiusure nel weekend e pause creano buchi nelle candele. Tienine conto e segnala nei rischi gli eventi tipici (Fed/BCE, inflazione USA, scorte di petrolio del mercoledì, OPEC, dati sul lavoro USA).
- Funding rate e open interest (sezione "Derivati") sono contesto sul posizionamento, non un segnale di ingresso: la decisione resta sulla price action.
  · Funding molto positivo (long affollati) o molto negativo (short affollati) rispetto alla sua media: rischio di movimento brusco contro la folla; un trade nella stessa direzione della folla merita un voto più basso, uno contro la folla su un livello chiave può guadagnare una conferma.
  · Prezzo su e open interest su: nuove posizioni sostengono il movimento. Prezzo su e open interest giù: chiusure di short, movimento meno solido. Prezzo giù e open interest su: nuovi short. Prezzo giù e open interest giù: chiusure di long (spesso esaurimento).
  · Citali in score_for / score_against solo quando dicono qualcosa di chiaro; se mancano, non inventarli.
- Usa solo i dati forniti; se mancano informazioni importanti dillo nei rischi.

Voto di fattibilità (score, intero da 1 a 10): quanta convinzione c'è di poter fare il trade secondo il metodo.
- Conta le conferme INDIPENDENTI che convergono sulla stessa tesi: direzione del settimanale, bias del giornaliero, struttura 4h/1h, livello chiave (supporto/resistenza, Fibonacci, livello rotto e ritestato), conferma sul 15m, momentum (RSI, divergenze) e volumi, stop in un punto logico con R:R ampio.
- Più conferme allineate alzano il voto; timeframe in disaccordo, segnali contrari, livelli vicini che limitano il target, prezzo lontano dalla zona o eventi a rischio lo abbassano.
- Scala: 1–3 nessun vantaggio, 4–5 debole, 6–7 discreto, 8–9 forte, 10 eccezionale e raro. Non gonfiare il voto: con decision no_trade il voto è di regola 4 o meno.
- score_for: le conferme, una per voce, brevi e con i prezzi. score_against: i punti contrari, stesso formato.
"""

NUM_OR_NULL = {"anyOf": [{"type": "number"}, {"type": "null"}]}

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["trade", "no_trade"]},
        "summary": {"type": "string"},
        "weekly": {"type": "string"},
        "daily": {"type": "string"},
        "structure": {"type": "string"},
        "timing": {"type": "string"},
        "key_levels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "price": {"type": "number"},
                    "kind": {"type": "string", "enum": ["supporto", "resistenza", "altro"]},
                    "note": {"type": "string"},
                },
                "required": ["price", "kind", "note"],
                "additionalProperties": False,
            },
        },
        "setup": {"type": "string", "enum": list(SETUPS)},
        "side": {"type": "string", "enum": ["long", "short", "none"]},
        "entry": NUM_OR_NULL,
        "stop": NUM_OR_NULL,
        "tp1": NUM_OR_NULL,
        "target": NUM_OR_NULL,
        "cancel_above": NUM_OR_NULL,
        "cancel_below": NUM_OR_NULL,
        "expiry_hours": NUM_OR_NULL,
        "cancel_conditions": {"type": "array", "items": {"type": "string"}},
        "invalidation": {"type": "string"},
        "bias": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "score": {"type": "integer"},
        "score_for": {"type": "array", "items": {"type": "string"}},
        "score_against": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "decision", "summary", "weekly", "daily", "structure", "timing", "key_levels", "setup", "side",
        "entry", "stop", "tp1", "target", "cancel_above", "cancel_below", "expiry_hours",
        "cancel_conditions", "invalidation", "bias", "risks", "score", "score_for", "score_against",
    ],
    "additionalProperties": False,
}

_ai: Optional[AsyncAnthropic] = None


def get_ai() -> AsyncAnthropic:
    global _ai
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="Manca ANTHROPIC_API_KEY in backend/.env")
    if _ai is None:
        _ai = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _ai


async def ask_claude(system: str, user_text: str, schema: Optional[dict] = None,
                     model: str = "", effort: str = "") -> tuple[dict, dict]:
    """Richiesta a Claude con risposta JSON validata dallo schema; restituisce (risposta, uso token)"""
    try:
        async with get_ai().beta.messages.stream(
            model=model or AI_MODEL,
            max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": effort or AI_EFFORT, "format": {"type": "json_schema", "schema": schema or ANALYSIS_SCHEMA}},
            system=system,
            messages=[{"role": "user", "content": user_text}],
        ) as stream:
            response = await stream.get_final_message()
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=502, detail="Chiave ANTHROPIC_API_KEY non valida")
    except anthropic.RateLimitError:
        raise HTTPException(status_code=502, detail="Troppe richieste a Claude: riprova tra un minuto")
    except anthropic.APIStatusError as e:
        raise HTTPException(status_code=502, detail=f"Errore di Claude: {e.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(status_code=502, detail="Claude non raggiungibile: riprova più tardi")
    if response.stop_reason == "refusal":
        raise HTTPException(status_code=502, detail="Claude ha rifiutato la richiesta")
    if response.stop_reason == "max_tokens":
        raise HTTPException(status_code=502, detail="Risposta di Claude incompleta: riprova")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="Risposta di Claude non leggibile: riprova")
    usage = {
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return result, usage


class AnalysisIn(BaseModel):
    instrument: str
    note: str = Field(default="", max_length=2000)


async def market_context(inst: str) -> tuple[dict, list[str], list[str]]:
    """Prezzo attuale e candele di tutti i timeframe, già in testo per Claude"""
    ticker, *series = await asyncio.gather(
        fetch_ticker(inst), *(fetch_candles(inst, tf, n) for tf, n in ANALYSIS_CANDLES.items())
    )
    derivatives = await derivatives_info(inst, ticker["last"])
    now = datetime.now(TZ)
    header = [
        f"Strumento: {inst} (OKX). Ora attuale: {now.strftime('%A %d/%m/%Y %H:%M')} (ora italiana).",
        f"Ultimo prezzo {fmt_price(ticker['last'])} · 24h: apertura {fmt_price(ticker['open24h'])}, "
        f"massimo {fmt_price(ticker['high24h'])}, minimo {fmt_price(ticker['low24h'])}.",
    ]
    blocks = [candles_block(tf, candles) for tf, candles in zip(ANALYSIS_CANDLES, series)]
    if inst_type_of(inst) != "SPOT":
        blocks.append(derivatives_block(derivatives))
    return ticker, header, blocks


async def scan_reference(inst: str) -> Optional[dict]:
    """Voto che lo strumento aveva nell'ultima classifica di oggi (per confrontarlo con l'analisi completa)"""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    scan = await db.scans.find_one({"date": today, "status": "ok", "items.instrument": inst}, sort=[("created_at", -1)])
    if not scan:
        return None
    item = next(i for i in scan["items"] if i["instrument"] == inst)
    return {"scan_id": str(scan["_id"]), "created_at": iso(scan["created_at"]), "score": item["score"],
            "direction": item["direction"], "setup": item["setup"]}


@api.post("/analysis")
async def run_analysis(data: AnalysisIn, user: dict = Depends(get_current_user)):
    daily = await daily_status()
    if daily["stopped"]:
        raise HTTPException(status_code=409, detail=daily["message"])
    inst = data.instrument.strip().upper()
    settings = await load_settings()
    ticker, header, blocks = await market_context(inst)
    parts = header + [
        f"Capitale {settings['capital']:g} · rischio {settings['risk_pct']:g}% · leva {settings['leverage']:g}x.",
        f"Situazione di oggi: {daily['message']}",
    ]
    if data.note.strip():
        parts.append(f"Nota del trader per questa sessione: {data.note.strip()}")
    parts += blocks
    parts.append("Fai l'analisi top-down e proponi il piano della sessione (o nessun trade).")
    result, usage = await ask_claude(ANALYSIS_SYSTEM, "\n\n".join(parts))

    result["score"] = max(1, min(10, int(result["score"])))
    plan_issues: list[str] = []
    if result["decision"] == "trade":
        plan_issues = plan_problems(result["side"], result["entry"], result["stop"], result["tp1"], result["target"])
    expiry = None
    if result.get("expiry_hours"):
        expiry = iso(now_utc() + timedelta(hours=float(result["expiry_hours"])))
    doc = {
        "instrument": inst,
        "created_at": now_utc(),
        "price": ticker["last"],
        "note": data.note.strip(),
        "result": result,
        "expiry": expiry,
        "rr": rr_of(result["entry"], result["stop"], result["target"]),
        "plan_issues": plan_issues,
        "usage": usage,
        "scan_ref": await scan_reference(inst),
    }
    res = await db.analyses.insert_one(doc)
    doc["_id"] = res.inserted_id
    return analysis_out(doc)


def analysis_out(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    out["id"] = str(doc["_id"])
    out["created_at"] = iso(doc["created_at"])
    return out


@api.get("/analysis")
async def list_analyses(limit: int = 20, user: dict = Depends(get_current_user)):
    docs = await db.analyses.find().sort("created_at", -1).to_list(min(limit, 100))
    return [analysis_out(d) for d in docs]


@api.post("/analysis/{analysis_id}/dismiss")
async def dismiss_analysis(analysis_id: str, user: dict = Depends(get_current_user)):
    """Nasconde un'analisi dalla pagina Sessione (resta salvata)"""
    res = await db.analyses.update_one({"_id": oid(analysis_id)}, {"$set": {"dismissed": True}})
    if not res.matched_count:
        raise HTTPException(status_code=404, detail="Analisi non trovata")
    return {"ok": True}


@api.get("/analysis/{analysis_id}")
async def get_analysis(analysis_id: str, user: dict = Depends(get_current_user)):
    doc = await db.analyses.find_one({"_id": oid(analysis_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Analisi non trovata")
    return analysis_out(doc)


# ---------- Sessioni (piano, esito, diario) ----------

def rr_of(entry, stop, target) -> Optional[float]:
    if None in (entry, stop, target) or entry == stop:
        return None
    return round(abs(target - entry) / abs(entry - stop), 2)


def plan_problems(side, entry, stop, tp1, target) -> list[str]:
    """Controlli del piano: ordine dei livelli e rapporto rischio/rendimento minimo"""
    if side not in ("long", "short"):
        return ["Direzione mancante"]
    if None in (entry, stop, tp1, target):
        return ["Servono ingresso, stop, TP1 e target"]
    issues = []
    if side == "long" and not (stop < entry < tp1 <= target):
        issues.append("Per un long serve stop < ingresso < TP1 ≤ target")
    if side == "short" and not (stop > entry > tp1 >= target):
        issues.append("Per uno short serve stop > ingresso > TP1 ≥ target")
    rr = rr_of(entry, stop, target)
    if not issues and rr is not None and rr < MIN_RR:
        issues.append(f"Rapporto rischio/rendimento {rr:g}:1, sotto il minimo di {MIN_RR:g}:1: non si entra")
    return issues


def r_multiple(side: str, entry: float, stop: float, price: float) -> float:
    risk = abs(entry - stop)
    move = price - entry if side == "long" else entry - price
    return move / risk


class SessionIn(BaseModel):
    instrument: str
    side: Literal["long", "short", "none"]
    setup: str
    entry: Optional[float] = None
    stop: Optional[float] = None
    tp1: Optional[float] = None
    target: Optional[float] = None
    cancel_above: Optional[float] = None
    cancel_below: Optional[float] = None
    expiry: Optional[str] = None
    bias: str = ""
    note: str = ""
    analysis_id: Optional[str] = None

    @field_validator("setup")
    @classmethod
    def valid_setup(cls, v: str) -> str:
        if v not in SETUPS:
            raise ValueError("Setup non valido")
        return v


def session_out(doc: dict) -> dict:
    out = {k: v for k, v in doc.items() if k != "_id"}
    out["id"] = str(doc["_id"])
    for k in ("created_at", "filled_at", "closed_at", "updated_at"):
        if isinstance(doc.get(k), datetime):
            out[k] = iso(doc[k])
    out["rr"] = rr_of(doc.get("entry"), doc.get("stop"), doc.get("target"))
    if isinstance(doc.get("last_review_at"), datetime):
        out["last_review_at"] = iso(doc["last_review_at"])
    out["reviews"] = [{**r, "created_at": iso(r["created_at"])} for r in doc.get("reviews", [])]
    return out


def session_fields(data: SessionIn, settings: dict) -> dict:
    fields = data.model_dump()
    fields["instrument"] = data.instrument.strip().upper()
    if data.side == "none" or data.setup == "nessuno":
        fields.update(side="none", setup="nessuno", entry=None, stop=None, tp1=None, target=None,
                      cancel_above=None, cancel_below=None, expiry=None)
        return fields
    issues = plan_problems(data.side, data.entry, data.stop, data.tp1, data.target)
    if issues:
        raise HTTPException(status_code=400, detail=" · ".join(issues))
    fields["expiry"] = iso(parse_dt(data.expiry)) if data.expiry else None
    # Dimensione calcolata ora, con le impostazioni del momento: resta nel diario
    risk_usd = settings["capital"] * settings["risk_pct"] / 100
    qty = risk_usd / abs(data.entry - data.stop)
    fields["sizing"] = {
        "capital": settings["capital"], "risk_pct": settings["risk_pct"], "leverage": settings["leverage"],
        "risk_usd": round(risk_usd, 2), "quantity": qty,
        "notional": round(qty * data.entry, 2), "margin": round(qty * data.entry / settings["leverage"], 2),
    }
    return fields


@api.get("/sessions")
async def list_sessions(user: dict = Depends(get_current_user)):
    docs = await db.sessions.find().sort("created_at", -1).to_list(2000)
    return [session_out(d) for d in docs]


@api.post("/sessions")
async def create_session(data: SessionIn, user: dict = Depends(get_current_user)):
    daily = await daily_status()
    if daily["stopped"] and data.side != "none":
        raise HTTPException(status_code=409, detail=daily["message"])
    if data.side != "none" and await db.sessions.find_one({"status": {"$in": ["attesa", "aperta"]}}):
        raise HTTPException(status_code=409, detail="C'è già una sessione in corso: chiudila o annullala prima")
    fields = session_fields(data, await load_settings())
    fields["score"] = await analysis_score(data.analysis_id)
    last = await db.sessions.find_one(sort=[("n", -1)])
    doc = {
        **fields,
        "n": (last["n"] if last else 0) + 1,
        "status": "notrade" if fields["side"] == "none" else "attesa",
        "created_at": now_utc(),
    }
    if doc["status"] == "notrade":
        doc["closed_at"] = doc["created_at"]
    res = await db.sessions.insert_one(doc)
    doc["_id"] = res.inserted_id
    if data.analysis_id and ObjectId.is_valid(data.analysis_id):
        # L'analisi usata per una sessione non si ripropone più nella pagina
        await db.analyses.update_one({"_id": ObjectId(data.analysis_id)}, {"$set": {"used": True}})
    return session_out(doc)


async def analysis_score(analysis_id: Optional[str]) -> Optional[int]:
    """Voto di fattibilità dell'analisi da cui nasce il piano (resta nel diario per confrontarlo con l'esito)"""
    if not analysis_id or not ObjectId.is_valid(analysis_id):
        return None
    doc = await db.analyses.find_one({"_id": ObjectId(analysis_id)}, {"result.score": 1})
    return (doc or {}).get("result", {}).get("score")


REVIEW_SYSTEM = ANALYSIS_SYSTEM + """

RICONTROLLO DI UN ORDINE IN ATTESA
Ora non proponi un trade nuovo: ricontrolli un piano già deciso, con l'ordine limit inserito ma non ancora eseguito.
Rileggi il mercato con lo stesso metodo top-down e confrontalo con il piano originale. Domanda: vale ancora la pena aspettare che l'ordine entri, oppure lo scenario è cambiato?
- verdict "mantieni": la tesi regge, i livelli vanno bene così.
- verdict "modifica": la tesi regge ma i livelli vanno aggiornati (zona di ingresso spostata, stop o target da rivedere); proponi i nuovi livelli nei campi entry, stop, tp1, target, cancel_above, cancel_below, rispettando le regole (R:R minimo, ordine dei livelli). La direzione resta quella del piano.
- verdict "annulla": la tesi non regge più (struttura rotta, invalidazione toccata, setup scaduto, R:R non più sensato).
Con verdict diverso da "modifica" metti i livelli a null.
In changes elenca cosa è cambiato rispetto al piano, con i prezzi. Il voto (score) è la fattibilità di questo trade ADESSO, con gli stessi criteri dell'analisi.
"""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["mantieni", "modifica", "annulla"]},
        "summary": {"type": "string"},
        "changes": {"type": "array", "items": {"type": "string"}},
        "score": {"type": "integer"},
        "score_for": {"type": "array", "items": {"type": "string"}},
        "score_against": {"type": "array", "items": {"type": "string"}},
        "entry": NUM_OR_NULL,
        "stop": NUM_OR_NULL,
        "tp1": NUM_OR_NULL,
        "target": NUM_OR_NULL,
        "cancel_above": NUM_OR_NULL,
        "cancel_below": NUM_OR_NULL,
    },
    "required": ["verdict", "summary", "changes", "score", "score_for", "score_against",
                 "entry", "stop", "tp1", "target", "cancel_above", "cancel_below"],
    "additionalProperties": False,
}

# Ricontrolli in corso (per non lanciarne due insieme sulla stessa sessione)
REVIEWS_RUNNING: set[str] = set()
# Riferimenti ai ricontrolli automatici avviati: senza, Python può chiudere un task a metà
REVIEW_TASKS: set[asyncio.Task] = set()
REVIEW_RETRY_H = 1  # dopo un ricontrollo automatico fallito si riprova dopo un'ora


async def review_session(doc: dict, trigger: str) -> dict:
    """Ricontrolla con Claude un piano in attesa e salva il verdetto nella sessione"""
    sid = str(doc["_id"])
    if sid in REVIEWS_RUNNING:
        raise HTTPException(status_code=409, detail="Un ricontrollo di questa sessione è già in corso")
    REVIEWS_RUNNING.add(sid)
    try:
        inst = doc["instrument"]
        ticker, header, blocks = await market_context(inst)
        created = doc["created_at"] if doc["created_at"].tzinfo else doc["created_at"].replace(tzinfo=timezone.utc)
        hours = (now_utc() - created).total_seconds() / 3600
        plan = [
            f"PIANO ORIGINALE (sessione {doc['n']}, creato il {created.astimezone(TZ).strftime('%d/%m/%Y %H:%M')}, {hours:.0f} ore fa):",
            f"{doc['side'].upper()} · setup {SETUPS.get(doc['setup'], doc['setup'])} · ingresso limit {fmt_price(doc['entry'])} · "
            f"stop {fmt_price(doc['stop'])} · TP1 {fmt_price(doc['tp1'])} · target {fmt_price(doc['target'])}",
            f"Annulla sopra {fmt_price(doc.get('cancel_above'))} · annulla sotto {fmt_price(doc.get('cancel_below'))} · "
            f"scadenza {doc.get('expiry') or 'nessuna'}",
            f"Bias: {doc.get('bias') or '-'}",
        ]
        if doc.get("analysis_id") and ObjectId.is_valid(doc["analysis_id"]):
            a = await db.analyses.find_one({"_id": ObjectId(doc["analysis_id"])})
            if a:
                r = a["result"]
                plan.append(f"Analisi di partenza (voto {r.get('score', 'n/d')}/10): {r.get('summary', '')}")
                plan.append(f"Invalidazione prevista: {r.get('invalidation', '-')}")
        if doc.get("reviews"):
            last = doc["reviews"][-1]["result"]
            plan.append(f"Ultimo ricontrollo: {last['verdict']} (voto {last['score']}/10): {last['summary']}")
        parts = header + plan + blocks + ["Ricontrolla il piano: mantieni, modifica i livelli o annulla?"]
        result, usage = await ask_claude(REVIEW_SYSTEM, "\n\n".join(parts), REVIEW_SCHEMA)
        result["score"] = max(1, min(10, int(result["score"])))
        issues: list[str] = []
        if result["verdict"] == "modifica":
            issues = plan_problems(doc["side"], result["entry"], result["stop"], result["tp1"], result["target"])
        review = {
            "created_at": now_utc(), "trigger": trigger, "price": ticker["last"], "hours": round(hours, 1),
            "result": result, "plan_issues": issues, "usage": usage,
        }
        await db.sessions.update_one({"_id": doc["_id"]}, {"$push": {"reviews": review}, "$set": {"last_review_at": review["created_at"]}})
        return review
    finally:
        REVIEWS_RUNNING.discard(sid)


VERDICT_ICONS = {"mantieni": "🟢 MANTIENI", "modifica": "🟡 MODIFICA", "annulla": "🔴 ANNULLA"}


def review_text(doc: dict, review: dict) -> str:
    r = review["result"]
    lines = [f"🔄 <b>Ricontrollo sessione {doc['n']}</b> · {instrument_label(doc['instrument'])}",
             f"{VERDICT_ICONS[r['verdict']]} · voto {r['score']}/10 · prezzo {fmt_price(review['price'])}",
             html.escape(r["summary"])]
    lines += [f"• {html.escape(c)}" for c in r["changes"][:4]]
    if r["verdict"] == "modifica" and r["entry"] is not None:
        lines.append(f"Nuovi livelli: ingresso {fmt_price(r['entry'])} · stop {fmt_price(r['stop'])} · "
                     f"TP1 {fmt_price(r['tp1'])} · target {fmt_price(r['target'])}")
    lines.append("Decidi tu nel cruscotto e su OKX.")
    return "\n".join(lines)


async def auto_reviews() -> None:
    """Ricontrollo automatico dei piani in attesa ogni N ore (impostazioni); il verdetto arriva su Telegram"""
    hours = float((await load_settings()).get("review_every_hours") or 0)
    if hours <= 0 or not ANTHROPIC_API_KEY:
        return
    async for doc in db.sessions.find({"status": "attesa"}):
        last = doc.get("last_review_at") or doc["created_at"]
        last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        if (now_utc() - last).total_seconds() >= hours * 3600 and str(doc["_id"]) not in REVIEWS_RUNNING:
            task = asyncio.create_task(auto_review(doc, hours))
            REVIEW_TASKS.add(task)
            task.add_done_callback(REVIEW_TASKS.discard)


async def auto_review(doc: dict, hours: float) -> None:
    try:
        review = await review_session(doc, "automatico")
    except Exception as e:
        detail = e.detail if isinstance(e, HTTPException) else repr(e)
        print(f"[ricontrollo] sessione {doc.get('n')} non riuscito: {detail}", flush=True)
        # Nuovo tentativo dopo un'ora (non a ogni ciclo, non dopo un giorno intero)
        retry_from = now_utc() - timedelta(hours=max(hours - REVIEW_RETRY_H, 0))
        await db.sessions.update_one({"_id": doc["_id"]}, {"$set": {"last_review_at": retry_from}})
        return
    await notify("ricontrollo", doc["instrument"], review_text(doc, review))


@api.post("/sessions/{session_id}/review")
async def review_session_route(session_id: str, user: dict = Depends(get_current_user)):
    doc = await find_session(session_id)
    if doc["status"] != "attesa":
        raise HTTPException(status_code=409, detail="Si ricontrolla solo un ordine in attesa di esecuzione")
    await review_session(doc, "manuale")
    return session_out(await find_session(session_id))


async def find_session(session_id: str) -> dict:
    doc = await db.sessions.find_one({"_id": oid(session_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Sessione non trovata")
    return doc


@api.put("/sessions/{session_id}")
async def update_session(session_id: str, data: SessionIn, user: dict = Depends(get_current_user)):
    doc = await find_session(session_id)
    if doc["status"] != "attesa":
        raise HTTPException(status_code=409, detail="Si può modificare solo un piano in attesa di esecuzione")
    fields = session_fields(data, await load_settings())
    fields["score"] = await analysis_score(data.analysis_id)
    fields["updated_at"] = now_utc()
    await db.sessions.update_one({"_id": doc["_id"]}, {"$set": fields})
    return session_out(await find_session(session_id))


class NoteIn(BaseModel):
    note: str = Field(max_length=5000)


@api.put("/sessions/{session_id}/note")
async def update_session_note(session_id: str, data: NoteIn, user: dict = Depends(get_current_user)):
    doc = await find_session(session_id)
    await db.sessions.update_one({"_id": doc["_id"]}, {"$set": {"note": data.note}})
    return session_out(await find_session(session_id))


class FillIn(BaseModel):
    filled_at: Optional[str] = None


@api.post("/sessions/{session_id}/fill")
async def fill_session(session_id: str, data: FillIn, user: dict = Depends(get_current_user)):
    doc = await find_session(session_id)
    if doc["status"] != "attesa":
        raise HTTPException(status_code=409, detail="La sessione non è in attesa di esecuzione")
    filled_at = parse_dt(data.filled_at) or now_utc()
    await db.sessions.update_one({"_id": doc["_id"]}, {"$set": {"status": "aperta", "filled_at": filled_at}})
    return session_out(await find_session(session_id))


class CloseIn(BaseModel):
    outcome: Literal["stop", "tp1_pareggio", "tp1_target", "manuale"]
    exit_price: Optional[float] = None
    tp1_taken: bool = False  # uscita manuale dopo aver già preso metà a TP1


@api.post("/sessions/{session_id}/close")
async def close_session(session_id: str, data: CloseIn, user: dict = Depends(get_current_user)):
    doc = await find_session(session_id)
    if doc["status"] != "aperta":
        raise HTTPException(status_code=409, detail="La sessione non ha una posizione aperta")
    side, entry, stop = doc["side"], doc["entry"], doc["stop"]
    r_tp1 = r_multiple(side, entry, stop, doc["tp1"])
    if data.outcome == "stop":
        r, exit_price = -1.0, stop
    elif data.outcome == "tp1_pareggio":
        r, exit_price = r_tp1 / 2, entry
    elif data.outcome == "tp1_target":
        r, exit_price = (r_tp1 + r_multiple(side, entry, stop, doc["target"])) / 2, doc["target"]
    else:
        if data.exit_price is None:
            raise HTTPException(status_code=400, detail="Indica il prezzo di uscita")
        r_exit = r_multiple(side, entry, stop, data.exit_price)
        r, exit_price = ((r_tp1 + r_exit) / 2 if data.tp1_taken else r_exit), data.exit_price
    await db.sessions.update_one({"_id": doc["_id"]}, {"$set": {
        "status": "chiusa", "closed_at": now_utc(), "outcome": data.outcome,
        "exit": exit_price, "tp1_taken": data.tp1_taken or data.outcome != "stop", "r": round(r, 2),
    }})
    return session_out(await find_session(session_id))


class CancelIn(BaseModel):
    reason: str = Field(default="", max_length=500)


@api.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str, data: CancelIn, user: dict = Depends(get_current_user)):
    doc = await find_session(session_id)
    if doc["status"] != "attesa":
        raise HTTPException(status_code=409, detail="Si annulla solo un ordine non ancora eseguito")
    await db.sessions.update_one({"_id": doc["_id"]}, {"$set": {
        "status": "annullata", "closed_at": now_utc(), "cancel_reason": data.reason,
    }})
    return session_out(await find_session(session_id))


@api.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: dict = Depends(get_current_user)):
    doc = await find_session(session_id)
    await db.sessions.delete_one({"_id": doc["_id"]})
    return {"ok": True}


async def daily_status() -> dict:
    """Stop giornaliero: 2 perdite di fila oppure -2R nella giornata (ora italiana)"""
    start = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    docs = await db.sessions.find({"status": "chiusa", "closed_at": {"$gte": start}}).sort("closed_at", 1).to_list(100)
    r_today = round(sum(d.get("r", 0) for d in docs), 2)
    losses_in_row = 0
    for d in docs:
        losses_in_row = losses_in_row + 1 if d.get("r", 0) < 0 else 0
    stopped = losses_in_row >= DAILY_MAX_LOSSES_IN_ROW or r_today <= DAILY_MAX_LOSS_R
    if stopped:
        message = f"Stop giornaliero raggiunto ({losses_in_row} perdite di fila, {r_today:+g} R oggi): per oggi niente nuovi trade."
    elif docs:
        message = f"Oggi {len(docs)} trade chiusi, {r_today:+g} R, {losses_in_row} perdite di fila."
    else:
        message = "Nessun trade chiuso oggi."
    return {"trades": len(docs), "r_today": r_today, "losses_in_row": losses_in_row, "stopped": stopped, "message": message}


@api.get("/sessions/today")
async def sessions_today(user: dict = Depends(get_current_user)):
    return await daily_status()


@api.get("/stats")
async def stats(user: dict = Depends(get_current_user)):
    docs = await db.sessions.find().to_list(5000)

    def summarize(items: list[dict]) -> dict:
        closed = [d for d in items if d["status"] == "chiusa"]
        wins = [d for d in closed if d.get("r", 0) > 0]
        total_r = round(sum(d.get("r", 0) for d in closed), 2)
        return {
            "sessions": len(items),
            "trades": len(closed),
            "no_trade": sum(1 for d in items if d["status"] == "notrade"),
            "cancelled": sum(1 for d in items if d["status"] == "annullata"),
            "win_rate": round(100 * len(wins) / len(closed), 1) if closed else None,
            "total_r": total_r,
            "avg_r": round(total_r / len(closed), 2) if closed else None,
        }

    def grouped(key) -> list[dict]:
        groups: dict[str, list] = {}
        for d in docs:
            groups.setdefault((key(d) if callable(key) else d.get(key)) or "-", []).append(d)
        return sorted(({"key": k, **summarize(v)} for k, v in groups.items()), key=lambda g: -g["sessions"])

    def score_band(d: dict) -> Optional[str]:
        score = d.get("score")
        if score is None:
            return None
        return "8-10" if score >= 8 else "6-7" if score >= 6 else "1-5"

    by_score = sorted(grouped(score_band), key=lambda g: g["key"], reverse=True)
    pairs = [a async for a in db.analyses.find({"scan_ref": {"$ne": None}, "result.score": {"$exists": True}}).sort("created_at", -1)]
    pairs = [a for a in pairs if a.get("scan_ref")]
    diffs = [a["result"]["score"] - a["scan_ref"]["score"] for a in pairs]
    scan_vs_analysis = {
        "count": len(pairs),
        "avg_diff": round(sum(diffs) / len(diffs), 2) if diffs else None,       # negativo = l'analisi completa è più severa
        "avg_abs_diff": round(sum(abs(x) for x in diffs) / len(diffs), 2) if diffs else None,
        "big_gaps": sum(1 for x in diffs if abs(x) >= 3),
        "recent": [
            {"instrument": a["instrument"], "created_at": iso(a["created_at"]), "scan_score": a["scan_ref"]["score"],
             "scan_direction": a["scan_ref"]["direction"], "analysis_score": a["result"]["score"],
             "analysis_decision": a["result"]["decision"], "analysis_side": a["result"]["side"]}
            for a in pairs[:10]
        ],
    }
    return {"overall": summarize(docs), "by_instrument": grouped("instrument"), "by_setup": grouped("setup"), "by_score": by_score,
            "scan_vs_analysis": scan_vs_analysis}


EXPORT_FIELDS = [
    "n", "created_at", "instrument", "side", "setup", "status", "entry", "stop", "tp1", "target",
    "cancel_above", "cancel_below", "expiry", "filled_at", "closed_at", "outcome", "exit", "r", "rr", "score", "bias", "note",
]


@api.get("/sessions/export")
async def export_sessions(format: Literal["csv", "json"] = "csv", user: dict = Depends(get_current_user)):
    docs = [session_out(d) for d in await db.sessions.find().sort("n", 1).to_list(5000)]
    stamp = datetime.now(TZ).strftime("%Y-%m-%d")
    if format == "json":
        body = json.dumps([{k: v for k, v in d.items() if k != "id"} for d in docs], ensure_ascii=False, indent=2)
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="diario-{stamp}.json"'})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, extrasaction="ignore", delimiter=";")
    writer.writeheader()
    for d in docs:
        writer.writerow({k: ("" if d.get(k) is None else d.get(k)) for k in EXPORT_FIELDS})
    return Response("﻿" + buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="diario-{stamp}.csv"'})


class ImportedSession(BaseModel):
    instrument: str
    side: Literal["long", "short", "none"]
    setup: str = "nessuno"
    status: Literal["attesa", "aperta", "chiusa", "annullata", "notrade"]
    created_at: str
    entry: Optional[float] = None
    stop: Optional[float] = None
    tp1: Optional[float] = None
    target: Optional[float] = None
    cancel_above: Optional[float] = None
    cancel_below: Optional[float] = None
    expiry: Optional[str] = None
    filled_at: Optional[str] = None
    closed_at: Optional[str] = None
    outcome: Optional[str] = None
    exit: Optional[float] = None
    r: Optional[float] = None
    score: Optional[int] = None
    bias: str = ""
    note: str = ""


@api.post("/sessions/import")
async def import_sessions(items: list[ImportedSession], user: dict = Depends(get_current_user)):
    """Importa sessioni da un file JSON (stesso formato dell'esportazione); salta quelle già presenti"""
    added = 0
    last = await db.sessions.find_one(sort=[("n", -1)])
    n = last["n"] if last else 0
    for item in items:
        if item.setup not in SETUPS:
            raise HTTPException(status_code=400, detail=f"Setup non valido: {item.setup}")
        doc = item.model_dump()
        doc["instrument"] = item.instrument.strip().upper()
        for k in ("created_at", "filled_at", "closed_at"):
            doc[k] = parse_dt(doc[k]) if doc[k] else None
        if await db.sessions.find_one({"instrument": doc["instrument"], "created_at": doc["created_at"]}):
            continue
        n += 1
        doc["n"] = n
        await db.sessions.insert_one(doc)
        added += 1
    return {"added": added, "skipped": len(items) - added}


# ---------- Notifiche Telegram sulle posizioni OKX ----------

def telegram_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


_tg_http: Optional[httpx.AsyncClient] = None


async def send_telegram(text: str) -> None:
    global _tg_http
    if not telegram_configured():
        raise HTTPException(status_code=503, detail="Telegram non configurato: mancano TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID in backend/.env")
    if _tg_http is None:
        _tg_http = httpx.AsyncClient(base_url="https://api.telegram.org", timeout=15)
    try:
        r = await _tg_http.post(f"/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
        body = r.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(status_code=502, detail="Telegram non raggiungibile")
    if not body.get("ok"):
        raise HTTPException(status_code=502, detail=f"Telegram: {body.get('description') or 'errore'}")


async def notify(kind: str, inst: str, text: str) -> None:
    """Invia la notifica e la registra; un errore di Telegram non deve fermare il controllo"""
    doc = {"kind": kind, "inst": inst, "text": text, "created_at": now_utc(), "sent": False, "error": ""}
    try:
        await send_telegram(text)
        doc["sent"] = True
    except HTTPException as e:
        doc["error"] = str(e.detail)
        print(f"[notifiche] {kind} {inst}: {e.detail}", flush=True)
    await db.notifications.insert_one(doc)


def money(x: Optional[float]) -> str:
    return "n/d" if x is None else f"{x:+.2f} $"


async def active_session_for(inst: str) -> Optional[dict]:
    return await db.sessions.find_one({"instrument": inst, "status": {"$in": ["attesa", "aperta"]}})


async def last_closed(inst: str) -> Optional[dict]:
    """Ultima chiusura (anche parziale) registrata da OKX per lo strumento"""
    rows = await okx_private_get("/api/v5/account/positions-history", {"instId": inst, "limit": "1"})
    return rows[0] if rows else None


async def check_positions() -> None:
    """Confronta le posizioni OKX con lo stato salvato e notifica: eseguito, chiusura (stop / take profit), guadagno oltre soglia"""
    settings = await load_settings()
    threshold = float(settings.get("profit_alert_usd") or 40)
    positions = {
        p["instId"]: p for p in await okx_private_get("/api/v5/account/positions") if fnum(p.get("pos"))
    }
    states = {d["_id"]: d async for d in db.monitor.find()}
    # Al primo avvio in assoluto si registra lo stato attuale senza notificare;
    # da lì in poi uno strumento mai visto parte da posizione zero
    first_run = not await db.monitor_meta.find_one({"_id": "init"})

    for inst in set(positions) | set(states):
        p = positions.get(inst)
        st = states.get(inst)
        size = abs(fnum(p.get("pos")) or 0) if p else 0.0
        prev = st["size"] if st else (None if first_run else 0.0)
        st = st or {}
        label = instrument_label(inst)
        session = await active_session_for(inst)

        if prev is None:
            # Primo controllo su questo strumento: si registra lo stato senza notificare
            await db.monitor.update_one({"_id": inst}, {"$set": {"size": size, "profit_alerted": False, "partial_taken": False}}, upsert=True)
            continue

        if prev == 0 and size > 0:
            side = "LONG" if (p.get("posSide") == "long" or (p.get("posSide") == "net" and (fnum(p.get("pos")) or 0) > 0)) else "SHORT"
            lines = [f"🟢 <b>Ordine eseguito</b> · {label}", f"{side} {size:g} contratti a {fmt_price(fnum(p.get('avgPx')))}"]
            if session and session.get("stop") is not None:
                lines.append(f"Stop {fmt_price(session['stop'])} · TP1 {fmt_price(session.get('tp1'))} · target {fmt_price(session.get('target'))}")
            lines.append("Controlla che lo stop sia inserito su OKX.")
            await notify("eseguito", inst, "\n".join(lines))
            await db.monitor.update_one({"_id": inst}, {"$set": {"size": size, "profit_alerted": False, "partial_taken": False}}, upsert=True)
            continue

        if size < prev:
            h = await last_closed(inst)
            pnl = fnum(h.get("realizedPnl")) if h else None
            close_px = fmt_price(fnum(h.get("closeAvgPx"))) if h else "n/d"
            if size > 0:
                title = "🟡 <b>Chiusura parziale</b> (TP1?)" if (pnl or 0) >= 0 else "🟠 <b>Chiusura parziale in perdita</b>"
                text = f"{title} · {label}\nChiusi {prev - size:g} contratti a {close_px}, restano {size:g}\nRisultato {money(pnl)}"
                if (pnl or 0) >= 0:
                    text += "\nRicorda: stop a pareggio sulla parte rimasta."
                await notify("parziale", inst, text)
                await db.monitor.update_one({"_id": inst}, {"$set": {"partial_taken": (pnl or 0) >= 0}})
            else:
                if h and h.get("type") in ("3", "4"):
                    title, kind = "🚨 <b>Posizione liquidata</b>", "liquidazione"
                elif (pnl or 0) < 0 and st.get("partial_taken"):
                    title, kind = "⚪ <b>Resto chiuso allo stop dopo TP1</b>", "pareggio"
                elif (pnl or 0) < 0:
                    title, kind = "🔴 <b>Stop loss</b>", "stop"
                else:
                    title, kind = "✅ <b>Take profit</b>", "take_profit"
                text = f"{title} · {label}\nPosizione chiusa a {close_px}\nRisultato {money(pnl)}"
                if session:
                    text += "\nRegistra l'esito nel cruscotto."
                await notify(kind, inst, text)
            reset = {"profit_alerted": False, "partial_taken": False} if size == 0 else {}
            await db.monitor.update_one({"_id": inst}, {"$set": {"size": size, **reset}})
            continue

        if size > prev:
            await db.monitor.update_one({"_id": inst}, {"$set": {"size": size}})

        if size > 0:
            upl = fnum(p.get("upl")) or 0
            if upl >= threshold and not st.get("profit_alerted"):
                text = (f"💰 <b>In guadagno di {upl:.2f} $</b> · {label}\n"
                        f"Ingresso {fmt_price(fnum(p.get('avgPx')))} · mark {fmt_price(fnum(p.get('markPx')))}")
                await notify("guadagno", inst, text)
                await db.monitor.update_one({"_id": inst}, {"$set": {"profit_alerted": True}})
            elif upl < threshold * 0.75 and st.get("profit_alerted"):
                # Si riarma se il guadagno torna sotto il 75% della soglia
                await db.monitor.update_one({"_id": inst}, {"$set": {"profit_alerted": False}})

    if first_run:
        await db.monitor_meta.update_one({"_id": "init"}, {"$set": {"at": now_utc()}}, upsert=True)


def instrument_label(inst: str) -> str:
    m = re.match(r"^([A-Z0-9]+)-([A-Z]+)_[A-Z]+_XPERP-\d{6}$", inst)
    return f"{m.group(1)}{m.group(2)} X-Perp" if m else inst


async def monitor_loop() -> None:
    while True:
        try:
            await snapshot_open_interest()
        except Exception as e:  # il ciclo non deve mai fermarsi
            print(f"[open interest] errore inatteso: {e!r}", flush=True)
        try:
            await auto_reviews()
            await maybe_daily_scan()
        except Exception as e:  # il ciclo non deve mai fermarsi
            print(f"[ricontrollo] errore inatteso: {e!r}", flush=True)
        if okx_account_configured():
            try:
                await check_positions()
            except HTTPException as e:
                print(f"[notifiche] controllo non riuscito: {e.detail}", flush=True)
            except Exception as e:  # il ciclo non deve mai fermarsi
                print(f"[notifiche] errore inatteso: {e!r}", flush=True)
        await asyncio.sleep(MONITOR_INTERVAL_S)


@api.post("/notifications/test")
async def test_notification(user: dict = Depends(get_current_user)):
    await send_telegram("🔔 Notifica di prova dal cruscotto di trading: Telegram funziona.")
    return {"ok": True}


@api.get("/notifications")
async def list_notifications(user: dict = Depends(get_current_user)):
    docs = await db.notifications.find().sort("created_at", -1).to_list(30)
    return [{"kind": d["kind"], "inst": d["inst"], "text": d["text"], "sent": d["sent"], "error": d.get("error", ""),
             "created_at": iso(d["created_at"])} for d in docs]


# ---------- Classifica del mattino ----------

SCAN_MAX_LIQUID = 80     # strumenti più scambiati che passano al filtro tecnico
SCAN_MIN_VOLUME_USD = 50_000
SCAN_TO_AI = 12          # migliori del filtro tecnico che legge Claude
SCAN_TELEGRAM_TOP = 5
SCAN_CANDLES = {"1D": 120, "4H": 120, "1H": 100}
SCAN_AI_CANDLES = {"1D": 60, "4H": 60, "1H": 48}
SCAN_WEEKLY = 52  # settimanale (un anno) solo per i candidati che legge Claude
SCANS_RUNNING: set[str] = set()
SCAN_TASKS: set[asyncio.Task] = set()


def atr(candles: list[dict], n: int = 14) -> Optional[float]:
    trs = [max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])) for p, c in zip(candles, candles[1:])]
    return sum(trs[-n:]) / n if len(trs) >= n else None


def trend_of(candles: list[dict]) -> int:
    """+1 rialzista (prezzo > SMA20 > SMA50), -1 ribassista, 0 laterale"""
    closes = [c["c"] for c in candles if c["confirmed"]]
    s20, s50 = sma(closes, 20), sma(closes, 50)
    if not closes or s20 is None or s50 is None:
        return 0
    if closes[-1] > s20 > s50:
        return 1
    if closes[-1] < s20 < s50:
        return -1
    return 0


def swing_levels(candles: list[dict], k: int = 2) -> list[float]:
    """Massimi e minimi evidenti (più alti/bassi delle k candele a sinistra e a destra)"""
    out = []
    for i in range(k, len(candles) - k):
        window = candles[i - k:i + k + 1]
        if candles[i]["h"] == max(c["h"] for c in window):
            out.append(candles[i]["h"])
        if candles[i]["l"] == min(c["l"] for c in window):
            out.append(candles[i]["l"])
    return out


def technical_score(d1: list[dict], h4: list[dict], h1: list[dict]) -> dict:
    """Punteggio meccanico 0-100: trend allineato, prezzo vicino a un livello, spazio per il target, momentum"""
    td, t4 = trend_of(d1), trend_of(h4)
    direction = td or t4
    atr4 = atr(h4)
    price = h1[-1]["c"] if h1 else None
    result = {"direction": "none", "score": 0, "trend_1d": td, "trend_4h": t4, "level": None, "target": None,
              "rr": None, "dist_atr": None, "rsi_1h": None}
    if not direction or not atr4 or not price:
        return result
    levels = swing_levels(h4[-90:]) + swing_levels(d1[-60:])
    trend_pts = 30 if td and td == t4 else 15 if td else 10
    if direction > 0:
        below, above = [x for x in levels if x < price], [x for x in levels if x > price]
        level = max(below) if below else None
        target = min((x for x in above if x > price + 0.5 * atr4), default=None)
    else:
        below, above = [x for x in levels if x < price], [x for x in levels if x > price]
        level = min(above) if above else None
        target = max((x for x in below if x < price - 0.5 * atr4), default=None)
    if level is None:
        return {**result, "direction": "long" if direction > 0 else "short", "score": trend_pts}
    dist = abs(price - level) / atr4
    prox_pts = 30 if dist <= 0.5 else 22 if dist <= 1 else 12 if dist <= 2 else 0
    risk = 0.5 * atr4  # stop mezzo ATR oltre il livello
    rr = abs(target - level) / risk if target is not None else 3.0  # nessun livello sulla strada: spazio libero
    rr_pts = 20 if rr >= 3 else 15 if rr >= 2 else 6 if rr >= 1.5 else 0
    r1 = rsi([c["c"] for c in h1 if c["confirmed"]])
    if r1 is None:
        mom_pts = 5
    elif direction > 0:
        mom_pts = 10 if 35 <= r1 <= 65 else 0 if r1 > 75 else 5
    else:
        mom_pts = 10 if 35 <= r1 <= 65 else 0 if r1 < 25 else 5
    return {
        **result, "direction": "long" if direction > 0 else "short",
        "score": round((trend_pts + prox_pts + rr_pts + mom_pts) / 90 * 100),
        "level": level, "target": target, "rr": round(rr, 2), "dist_atr": round(dist, 2),
        "rsi_1h": round(r1, 1) if r1 is not None else None,
    }


SCAN_SYSTEM = f"""Sei l'analista di un trader discrezionale che opera a sessione su OKX (X-Perp, margine isolato), solo price action.
Ogni mattina ricevi i candidati migliori di un filtro automatico su molti strumenti: devi fare una lettura VELOCE di ciascuno e dire quanto vale la pena studiarlo oggi. Non serve un piano completo: il trader farà poi un'analisi approfondita sullo strumento scelto.

Metodo: settimanale/giornaliero per direzione e zone, 4h/1h per la struttura. Setup ammessi: retest_rottura, pullback_trend, sweep_liquidita, rottura_range, nessuno.
Regole fisse: R:R minimo {MIN_RR:g}:1, stop in un punto logico, ingresso limit su un livello.

Per ogni candidato:
- score 1-10: fattibilità di un trade OGGI secondo il metodo. Conta le conferme indipendenti che convergono (trend dei timeframe, livello chiave vicino, spazio fino al target, momentum, funding e open interest come contesto). Scala: 1-3 nessun vantaggio, 4-5 debole, 6-7 discreto, 8-9 forte, 10 raro. Non gonfiare: è normale che la maggior parte prenda 5 o meno.
- direction long/short (none se non c'è un'idea), setup, level: il prezzo da guardare per l'ingresso (null se none).
- note: una frase, con i prezzi, su cosa aspettare.
Il settimanale decide la direzione di fondo: un'idea contro il settimanale merita un voto basso. Giudica solo dalle candele e dai dati forniti. In market_note una frase sul contesto generale (es. crypto tutte deboli, dollaro forte).
Scrivi in italiano semplice."""


def scan_schema(instruments: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "instrument": {"type": "string", "enum": instruments},
                        "score": {"type": "integer"},
                        "direction": {"type": "string", "enum": ["long", "short", "none"]},
                        "setup": {"type": "string", "enum": list(SETUPS)},
                        "level": NUM_OR_NULL,
                        "note": {"type": "string"},
                    },
                    "required": ["instrument", "score", "direction", "setup", "level", "note"],
                    "additionalProperties": False,
                },
            },
            "market_note": {"type": "string"},
        },
        "required": ["items", "market_note"],
        "additionalProperties": False,
    }


async def scan_universe(weekend: bool) -> tuple[list[dict], dict]:
    """Strumenti da analizzare: quelli in impostazioni, liquidi, e nel weekend solo crypto"""
    insts = [i for i in (await load_settings())["instruments"] if inst_type_of(i) != "SPOT"]
    stats = {"followed": len(insts), "not_crypto": 0, "illiquid": 0}
    by_type: dict[str, list[str]] = {}
    for i in insts:
        by_type.setdefault(inst_type_of(i), []).append(i)
    pool = []
    for inst_type, ids in by_type.items():
        rows, tickers = await asyncio.gather(okx_instruments(inst_type), okx_public("/api/v5/market/tickers", {"instType": inst_type}))
        category = {r["instId"]: r.get("instCategory", "") for r in rows}
        tick = {t["instId"]: t for t in tickers}
        for i in ids:
            if weekend and category.get(i) not in ("1", ""):
                stats["not_crypto"] += 1
                continue
            t = tick.get(i)
            last, vol = fnum(t.get("last")) if t else None, fnum(t.get("volCcy24h")) if t else None
            volume_usd = (last or 0) * (vol or 0)
            if volume_usd < SCAN_MIN_VOLUME_USD:
                stats["illiquid"] += 1
                continue
            pool.append({"inst": i, "volume_usd": volume_usd, "category": category.get(i, "")})
    pool.sort(key=lambda x: -x["volume_usd"])
    stats["too_many"] = max(0, len(pool) - SCAN_MAX_LIQUID)
    return pool[:SCAN_MAX_LIQUID], stats


async def run_scan(trigger: str) -> dict:
    """Classifica: filtro tecnico gratuito su tutti gli strumenti, poi lettura veloce di Claude sui migliori"""
    if "scan" in SCANS_RUNNING:
        raise HTTPException(status_code=409, detail="Una classifica è già in preparazione: attendi qualche minuto")
    SCANS_RUNNING.add("scan")
    started = now_utc()
    rome = datetime.now(TZ)
    weekend = rome.weekday() >= 5
    try:
        pool, stats = await scan_universe(weekend)
        gate = asyncio.Semaphore(5)  # OKX: 40 richieste di candele ogni 2 secondi

        async def candles_of(item: dict):
            async with gate:
                try:
                    series = []
                    for tf, n in SCAN_CANDLES.items():
                        series.append(await fetch_candles(item["inst"], tf, n))
                        await asyncio.sleep(0.25)
                    return item, series
                except HTTPException:
                    return item, None

        fetched = await asyncio.gather(*(candles_of(item) for item in pool))
        candidates, closed = [], 0
        for item, series in fetched:
            if not series or not all(series):
                continue
            d1, h4, h1 = series
            if (time.time() * 1000 - h1[-1]["t"]) > 3 * 3600_000:  # nessuna candela 1h recente: mercato chiuso
                closed += 1
                continue
            tech = technical_score(d1, h4, h1)
            if tech["direction"] != "none":
                candidates.append({**item, "tech": tech, "series": series, "price": h1[-1]["c"]})
        candidates.sort(key=lambda c: -c["tech"]["score"])
        top = candidates[:SCAN_TO_AI]
        stats.update({"liquid": len(pool), "closed": closed, "analyzed": len(candidates), "to_ai": len(top)})

        items, market_note, usage = [], "", None
        if top:
            # Ordine alfabetico: la posizione nell'elenco non deve suggerire una classifica
            top_sorted = sorted(top, key=lambda c: c["inst"])
            derivs = await asyncio.gather(*(derivatives_info(c["inst"], c["price"]) for c in top_sorted))

            async def weekly_of(inst: str) -> list[dict]:
                async with gate:
                    try:
                        return await fetch_candles(inst, "1W", SCAN_WEEKLY)
                    except HTTPException:
                        return []

            weeklies = await asyncio.gather(*(weekly_of(c["inst"]) for c in top_sorted))
            parts = [f"Ora: {rome.strftime('%A %d/%m/%Y %H:%M')} (ora italiana)." + (" Weekend: solo crypto." if weekend else "")]
            for c, d, w in zip(top_sorted, derivs, weeklies):
                # Solo dati di mercato: il punteggio del filtro automatico non va a Claude per non condizionarlo
                parts.append(f"# {c['inst']}\nPrezzo {fmt_price(c['price'])}")
                if w:
                    parts.append(candles_block("1W", w))
                for (tf, n), series in zip(SCAN_AI_CANDLES.items(), c["series"]):
                    parts.append(candles_block(tf, series[-n:]))
                parts.append(derivatives_block(d))
            parts.append("Valuta ogni candidato e restituisci un elemento per ciascuno.")
            result, usage = await ask_claude(SCAN_SYSTEM, "\n\n".join(parts), scan_schema([c["inst"] for c in top]),
                                             model=SCAN_AI_MODEL, effort=SCAN_AI_EFFORT)
            market_note = result["market_note"]
            tech_of = {c["inst"]: c for c in top}
            seen = set()
            for it in result["items"]:
                if it["instrument"] in seen or it["instrument"] not in tech_of:
                    continue
                seen.add(it["instrument"])
                c = tech_of[it["instrument"]]
                items.append({**it, "score": max(1, min(10, int(it["score"]))), "price": c["price"],
                              "tech_score": c["tech"]["score"], "volume_usd": round(c["volume_usd"])})
            items.sort(key=lambda x: (-x["score"], -x["tech_score"]))
        doc = {"created_at": started, "date": rome.strftime("%Y-%m-%d"), "trigger": trigger, "weekend": weekend,
               "status": "ok", "stats": stats, "items": items, "market_note": market_note, "usage": usage,
               "duration_s": round((now_utc() - started).total_seconds())}
    except Exception as e:  # anche un errore inatteso viene salvato: così la classifica automatica non riparte a raffica
        detail = e.detail if isinstance(e, HTTPException) else f"errore inatteso: {e!r}"
        print(f"[classifica] non riuscita: {detail}", flush=True)
        doc = {"created_at": started, "date": rome.strftime("%Y-%m-%d"), "trigger": trigger, "weekend": weekend,
               "status": "errore", "error": str(detail), "items": [], "stats": {}}
    finally:
        SCANS_RUNNING.discard("scan")
    res = await db.scans.insert_one(doc)
    doc["_id"] = res.inserted_id
    return doc


def scan_out(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = {k: v for k, v in doc.items() if k != "_id"}
    out["id"] = str(doc["_id"])
    out["created_at"] = iso(doc["created_at"])
    return out


def scan_text(doc: dict) -> str:
    when = doc["created_at"].astimezone(TZ) if doc["created_at"].tzinfo else doc["created_at"].replace(tzinfo=timezone.utc).astimezone(TZ)
    head = f"🌅 <b>Classifica del mattino</b> · {when.strftime('%d/%m %H:%M')}" + (" · weekend, solo crypto" if doc.get("weekend") else "")
    if doc["status"] != "ok":
        return f"{head}\nNon riuscita: {html.escape(doc.get('error', ''))}"
    lines = [head]
    if doc.get("market_note"):
        lines.append(html.escape(doc["market_note"]))
    best = [i for i in doc["items"] if i["direction"] != "none"][:SCAN_TELEGRAM_TOP]
    if not best:
        lines.append("Nessun candidato interessante oggi: «nessun trade» è una scelta valida.")
    for n, it in enumerate(best, 1):
        level = f" · livello {fmt_price(it['level'])}" if it.get("level") is not None else ""
        lines.append(f"\n{n}. <b>{instrument_label(it['instrument'])}</b> · {it['score']}/10 · {it['direction'].upper()} · "
                     f"{SETUPS.get(it['setup'], it['setup'])}{level}\n{html.escape(it['note'])}")
    st = doc.get("stats", {})
    lines.append(f"\nAnalizzati {st.get('analyzed', 0)} strumenti, letti da Claude {st.get('to_ai', 0)}. Approfondisci con «Ragiona ora».")
    return "\n".join(lines)


async def maybe_daily_scan() -> None:
    """Classifica automatica una volta al giorno all'ora delle impostazioni (ora italiana)"""
    settings = await load_settings()
    if not settings.get("scan_enabled") or not ANTHROPIC_API_KEY or "scan" in SCANS_RUNNING:
        return
    now = datetime.now(TZ)
    if now.strftime("%H:%M") < settings.get("scan_time", "08:30"):
        return
    if await db.scans.find_one({"date": now.strftime("%Y-%m-%d"), "trigger": "automatico"}):
        return
    task = asyncio.create_task(auto_scan())
    SCAN_TASKS.add(task)
    task.add_done_callback(SCAN_TASKS.discard)


async def auto_scan() -> None:
    try:
        doc = await run_scan("automatico")
    except Exception as e:  # non deve mai fermare il ciclo
        print(f"[classifica] errore inatteso: {e!r}", flush=True)
        return
    await notify("classifica", "", scan_text(doc))


@api.post("/scans")
async def scan_now(user: dict = Depends(get_current_user)):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="Manca ANTHROPIC_API_KEY in backend/.env")
    return scan_out(await run_scan("manuale"))


@api.get("/scans/latest")
async def latest_scan(user: dict = Depends(get_current_user)):
    return {"scan": scan_out(await db.scans.find_one(sort=[("created_at", -1)])), "running": "scan" in SCANS_RUNNING}


app.include_router(api)


@app.on_event("startup")
async def create_indexes():
    await db.users.create_index("email", unique=True)
    await db.sessions.create_index("created_at")
    await db.analyses.create_index("created_at")
    await db.notifications.create_index("created_at")
    await db.scans.create_index("created_at")
    await db.oi_snapshots.create_index([("inst", 1), ("ts", -1)])
    await db.oi_snapshots.create_index("ts", expireAfterSeconds=8 * 86400)
    app.state.monitor = asyncio.create_task(monitor_loop())


@app.on_event("shutdown")
async def shutdown():
    if getattr(app.state, "monitor", None):
        app.state.monitor.cancel()
    if _okx_http is not None:
        await _okx_http.aclose()
    client.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8003")))

