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
OKX_BASE_URL = os.environ.get("OKX_BASE_URL", "https://www.okx.com")  # OKX Europa (my.okx.com): https://eea.okx.com
# Chiave API OKX con il solo permesso "Leggi": serve a mostrare conto, posizioni e ordini
OKX_API_KEY = os.environ.get("OKX_API_KEY", "")
OKX_API_SECRET = os.environ.get("OKX_API_SECRET", "")
OKX_API_PASSPHRASE = os.environ.get("OKX_API_PASSPHRASE", "")

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
    instruments: list[str] = Field(min_length=1, max_length=20)

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
            "okx_base_url": OKX_BASE_URL, "okx_account": okx_account_configured()}


@api.put("/settings")
async def put_settings(data: SettingsIn, user: dict = Depends(get_current_user)):
    results = await asyncio.gather(*(instrument_exists(i) for i in data.instruments))
    missing = [i for i, ok in zip(data.instruments, results) if not ok]
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
        _okx_http = httpx.AsyncClient(base_url=OKX_BASE_URL, timeout=15)
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


async def instrument_exists(inst: str) -> bool:
    inst_type = inst_type_of(inst)
    try:
        return bool(await okx_public("/api/v5/public/instruments", {"instType": inst_type, "instId": inst}))
    except HTTPException as e:
        if "51001" in str(e.detail):  # strumento inesistente
            return False
        raise


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
- Usa solo i dati forniti; se mancano informazioni importanti dillo nei rischi.
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
        "confidence": {"type": "string", "enum": ["bassa", "media", "alta"]},
    },
    "required": [
        "decision", "summary", "weekly", "daily", "structure", "timing", "key_levels", "setup", "side",
        "entry", "stop", "tp1", "target", "cancel_above", "cancel_below", "expiry_hours",
        "cancel_conditions", "invalidation", "bias", "risks", "confidence",
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


async def ask_claude(system: str, user_text: str) -> tuple[dict, dict]:
    """Richiesta a Claude con risposta JSON validata dallo schema; restituisce (risposta, uso token)"""
    try:
        async with get_ai().beta.messages.stream(
            model=AI_MODEL,
            max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": AI_EFFORT, "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA}},
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


@api.post("/analysis")
async def run_analysis(data: AnalysisIn, user: dict = Depends(get_current_user)):
    daily = await daily_status()
    if daily["stopped"]:
        raise HTTPException(status_code=409, detail=daily["message"])
    inst = data.instrument.strip().upper()
    settings = await load_settings()
    ticker, *series = await asyncio.gather(
        fetch_ticker(inst), *(fetch_candles(inst, tf, n) for tf, n in ANALYSIS_CANDLES.items())
    )
    now = datetime.now(TZ)
    parts = [
        f"Strumento: {inst} (OKX). Ora attuale: {now.strftime('%A %d/%m/%Y %H:%M')} (ora italiana).",
        f"Ultimo prezzo {fmt_price(ticker['last'])} · 24h: apertura {fmt_price(ticker['open24h'])}, "
        f"massimo {fmt_price(ticker['high24h'])}, minimo {fmt_price(ticker['low24h'])}.",
        f"Capitale {settings['capital']:g} · rischio {settings['risk_pct']:g}% · leva {settings['leverage']:g}x.",
        f"Situazione di oggi: {daily['message']}",
    ]
    if data.note.strip():
        parts.append(f"Nota del trader per questa sessione: {data.note.strip()}")
    parts += [candles_block(tf, candles) for tf, candles in zip(ANALYSIS_CANDLES, series)]
    parts.append("Fai l'analisi top-down e proponi il piano della sessione (o nessun trade).")
    result, usage = await ask_claude(ANALYSIS_SYSTEM, "\n\n".join(parts))

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
    return session_out(doc)


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

    def grouped(key: str) -> list[dict]:
        groups: dict[str, list] = {}
        for d in docs:
            groups.setdefault(d.get(key) or "-", []).append(d)
        return sorted(({"key": k, **summarize(v)} for k, v in groups.items()), key=lambda g: -g["sessions"])

    return {"overall": summarize(docs), "by_instrument": grouped("instrument"), "by_setup": grouped("setup")}


EXPORT_FIELDS = [
    "n", "created_at", "instrument", "side", "setup", "status", "entry", "stop", "tp1", "target",
    "cancel_above", "cancel_below", "expiry", "filled_at", "closed_at", "outcome", "exit", "r", "rr", "bias", "note",
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


app.include_router(api)


@app.on_event("startup")
async def create_indexes():
    await db.users.create_index("email", unique=True)
    await db.sessions.create_index("created_at")
    await db.analyses.create_index("created_at")


@app.on_event("shutdown")
async def shutdown():
    if _okx_http is not None:
        await _okx_http.aclose()
    client.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8003")))

