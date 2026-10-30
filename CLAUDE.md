# Cruscotto di trading

Piattaforma personale per il trading discrezionale **a sessione** su OKX: Claude legge il mercato (analisi top-down 1W → 1D → 4H/1H → 15m) e propone il setup del giorno o "nessun trade"; il cruscotto applica le regole fisse di rischio, calcola la dimensione e tiene il diario delle sessioni.

Il titolare non è uno sviluppatore: rispondi in **italiano**, spiega in modo semplice e indica sempre cosa deve fare sulla VPS dopo una modifica. Metodo, regole e storia del progetto: `docs/metodo-e-passaggio.md`.

## Principi da non violare

- **Nessun ordine inviato a OKX.** OKX si usa solo in lettura: dati pubblici e conto con chiave API "Leggi" (`okx_private_get` fa solo GET firmati). L'esecuzione la fa il titolare nell'app OKX.
- Il titolare usa **OKX Europa** (my.okx.com): API su `https://eea.okx.com` (`OKX_BASE_URL`). Codici strumento e chiavi API valgono solo su quel dominio.
- Le **regole fisse** sono costanti in `backend/server.py` (`MAX_RISK_PCT`, `MAX_LEVERAGE`, `MIN_RR`, stop giornaliero) e vengono applicate dal server: impostazioni, creazione delle sessioni e "Ragiona ora" le rispettano. Non renderle modificabili dall'interfaccia.
- Le stesse verifiche del piano esistono nel frontend (`src/lib/risk.ts`) solo per il riscontro immediato: tienile allineate a `plan_problems` del backend.
- Testi dell'interfaccia in italiano; nessun consiglio finanziario presentato come certezza.

## Struttura

- `backend/server.py`: tutto il backend FastAPI (Mongo via motor). Rotte `/api/...`: login (JWT, nessuna registrazione: utenti con `backend/create_user.py`), impostazioni, dati di mercato OKX (`/market/ticker|candles|instrument`), analisi con Claude (`POST /analysis`, risposta JSON validata da `ANALYSIS_SCHEMA`), sessioni (piano → eseguito → chiuso/annullato, R calcolati dal server), statistiche, esportazione CSV/JSON e importazione JSON, conto OKX in sola lettura (`GET /account`: saldo, posizioni, ordini in attesa, stop/TP, posizioni chiuse). Le impostazioni rifiutano codici strumento che non esistono su OKX.
- Variabili in `backend/.env` (non nel repository, modello in `backend/.env.example`): `MONGO_URL`, `DB_NAME`, `SECRET_KEY`, `PORT`, `ANTHROPIC_API_KEY`, `AI_MODEL`, `AI_EFFORT`, `OKX_BASE_URL`, `OKX_API_KEY`, `OKX_API_SECRET`, `OKX_API_PASSPHRASE`.
- `frontend/`: React 19 + Vite + TypeScript + Tailwind, tema scuro. Pagine in `src/pages/` (Sessione, Diario, Impostazioni, Login), componenti in `src/components/`, calcoli in `src/lib/` (`risk.ts` dimensionamento e regole, `planForm.ts` modulo del piano, `format.ts` formati italiani). Grafico: `lightweight-charts` v5 con orari in ora italiana. Alias `@` → `src/`.
- `deploy/`: servizio systemd e configurazione Nginx per la VPS.
- `docs/sessione-1-*.json`: la Sessione 1 del 29/10/2026 in due varianti (ordine annullato / eseguito e stop), da importare dal Diario.

## Comandi di verifica (prima di ogni push)

```bash
cd frontend && npm run build      # tsc -b && vite build
cd frontend && npx eslint src
backend/venv/bin/python -c "import ast; ast.parse(open('backend/server.py').read())"
```

Le dipendenze le installa `.claude/hooks/session-start.sh`. Se cambi le dipendenze del frontend, aggiorna e committa `frontend/package-lock.json`.

## Preview (screenshot per il titolare)

Da questo ambiente cloud `www.okx.com` di norma non è raggiungibile e MongoDB non c'è: il backend di preview usa `mongomock-motor`, **candele e conto OKX finti** e, senza `ANTHROPIC_API_KEY`, un **piano di prova fisso** (marcato "[PIANO DI PROVA…]"). Non presentare mai questi dati come reali.

```bash
backend/venv/bin/python .claude/preview/backend.py &     # 127.0.0.1:8003, utente demo@trading.local / demo12345
(cd frontend && npx vite --port 3000) &                  # proxy /api → 8003
NODE_PATH=$(npm root -g) node .claude/preview/screenshot.cjs / /percorso/out.png [larghezza] [altezza]
```

Screenshot nella scratchpad, anche a larghezza telefono (390×844).

## Git e rilascio

- Lavora sul ramo indicato dalla sessione, mai su `main`. Il titolare fa il merge della Pull Request; la VPS fa `git pull` di `main`.
- Non committare `.env`, `dist/`, `node_modules/`, `venv/`.

## Produzione (VPS)

- Stessa VPS Hostinger del CRM (Ubuntu 24.04, Python 3.12, MongoDB 7). Progetto in `/var/www/trading/`, servizio systemd `trading` (uvicorn su 127.0.0.1:8003), Nginx `/etc/nginx/sites-available/trading`, database `trading`, dominio `trading.31cantaricrm.it`.
- Sulla VPS girano anche il CRM (`cantari` 8001, `pulsa` 8002) e **Infismart** (`fenestro`, 8000): non dare mai istruzioni che li tocchino, a parte `systemctl reload nginx`.
- Il database `trading` va nel backup notturno `/usr/local/bin/backup-infismart.sh` (riga `mongodump --db trading`).

Aggiornamento dopo il merge su `main`:

```bash
cd /var/www/trading && git pull && cd frontend && npm run build && systemctl restart trading
```

Se cambia `frontend/package.json`: `cd /var/www/trading/frontend && rm -rf node_modules dist && npm install && npm run build`.
Se cambia `backend/requirements.txt`: `cd /var/www/trading/backend && venv/bin/pip install -r requirements.txt && systemctl restart trading`.
