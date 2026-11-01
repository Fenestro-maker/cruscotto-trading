"""Prova automatica delle rotte principali, come le chiama il sito (dati OKX e Claude simulati).

Uso: backend/venv/bin/python .claude/preview/smoke_test.py
Va lanciata prima di ogni push: verifica che ogni indirizzo /api risponda con la funzione giusta.
"""
import importlib.util
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location("preview", Path(__file__).with_name("backend.py"))
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)
server = preview.server

INST = "BTC-USD_UM_XPERP-310404"
failures: list[str] = []


def check(label: str, response, status: int = 200, key: str | None = None):
    ok = response.status_code == status and (key is None or key in response.json())
    print(f"{'ok ' if ok else 'ERR'} {label}: {response.status_code}")
    if not ok:
        failures.append(f"{label}: {response.status_code} {response.text[:300]}")
    return response


with TestClient(server.app) as c:
    token = check("login", c.post("/api/auth/login", json={"email": "demo@trading.local", "password": "demo12345"}), key="access_token").json()["access_token"]
    c.headers["Authorization"] = f"Bearer {token}"

    check("impostazioni", c.get("/api/settings"), key="rules")
    check("salva impostazioni", c.put("/api/settings", json={
        "capital": 1500, "risk_pct": 1, "leverage": 3, "instruments": [INST], "profit_alert_usd": 40, "review_every_hours": 24, "scan_enabled": False,
    }), key="instruments")
    xperps = check("elenco X-Perp", c.get("/api/market/xperps")).json()
    if INST not in xperps or any("_XPERP-" not in i for i in xperps):
        failures.append(f"elenco X-Perp inatteso: {xperps[:5]}")
    check("salva tutti gli X-Perp", c.put("/api/settings", json={
        "capital": 1500, "risk_pct": 1, "leverage": 3, "instruments": xperps, "profit_alert_usd": 40, "review_every_hours": 24, "scan_enabled": False,
    }), key="instruments")
    check("codice inesistente rifiutato", c.put("/api/settings", json={
        "capital": 1500, "risk_pct": 1, "leverage": 3, "instruments": [INST, "PIPPO-USD_UM_XPERP-310404"],
        "profit_alert_usd": 40, "review_every_hours": 24, "scan_enabled": False,
    }), status=400)
    check("prezzo", c.get("/api/market/ticker", params={"inst": INST}), key="last")
    check("candele", c.get("/api/market/candles", params={"inst": INST, "tf": "1H"}))
    check("contratto", c.get("/api/market/instrument", params={"inst": INST}), key="ct_val")
    check("funding e open interest", c.get("/api/market/derivatives", params={"inst": INST}), key="funding")

    analysis = check("ragiona ora", c.post("/api/analysis", json={"instrument": INST, "note": ""}), key="result").json()
    check("elenco analisi", c.get("/api/analysis"))
    r = analysis["result"]
    session = check("avvia sessione", c.post("/api/sessions", json={
        "instrument": INST, "side": r["side"], "setup": r["setup"], "entry": r["entry"], "stop": r["stop"],
        "tp1": r["tp1"], "target": r["target"], "cancel_above": None, "cancel_below": None, "expiry": None,
        "bias": "", "note": "", "analysis_id": analysis["id"],
    }), key="id").json()
    used = next(a for a in c.get("/api/analysis").json() if a["id"] == analysis["id"])
    if not used.get("used"):
        failures.append("l'analisi usata per la sessione non risulta segnata come usata")
    check("ricontrollo", c.post(f"/api/sessions/{session['id']}/review"), key="reviews")
    check("ordine eseguito", c.post(f"/api/sessions/{session['id']}/fill", json={}))
    check("chiusura", c.post(f"/api/sessions/{session['id']}/close", json={"outcome": "tp1_target"}), key="r")
    check("oggi", c.get("/api/sessions/today"), key="stopped")
    check("statistiche", c.get("/api/stats"), key="by_score")
    exported = check("esporta json", c.get("/api/sessions/export", params={"format": "json"})).json()
    check("esporta csv", c.get("/api/sessions/export", params={"format": "csv"}))
    check("importa", c.post("/api/sessions/import", json=exported), key="skipped")
    other = check("seconda analisi", c.post("/api/analysis", json={"instrument": INST, "note": ""}), key="id").json()
    check("nascondi analisi", c.post(f"/api/analysis/{other['id']}/dismiss"))
    # All'avvio può partire da sola la classifica automatica del giorno: si aspetta che finisca
    for _ in range(60):
        if not c.get("/api/scans/latest").json()["running"]:
            break
        time.sleep(1)
    scan = check("classifica", c.post("/api/scans"), key="items").json()
    if scan.get("status") != "ok" or not scan["items"]:
        failures.append(f"classifica vuota o non riuscita: {scan.get('error')}")
    check("ultima classifica", c.get("/api/scans/latest"), key="scan")
    if scan.get("items"):
        picked = scan["items"][0]["instrument"]
        linked = check("analisi su uno strumento in classifica", c.post("/api/analysis", json={"instrument": picked, "note": ""}), key="scan_ref").json()
        if not linked.get("scan_ref") or linked["scan_ref"]["score"] != scan["items"][0]["score"]:
            failures.append(f"voto della classifica non collegato all'analisi: {linked.get('scan_ref')}")
        cmp = check("confronto nel diario", c.get("/api/stats"), key="scan_vs_analysis").json()["scan_vs_analysis"]
        if cmp["count"] < 1:
            failures.append("confronto classifica/analisi vuoto")
    check("conto OKX", c.get("/api/account"), key="configured")
    check("notifiche", c.get("/api/notifications"))

if failures:
    print("\nPROVA NON SUPERATA:")
    print("\n".join(f"- {f}" for f in failures))
    sys.exit(1)
print("\nTutte le rotte rispondono correttamente.")
