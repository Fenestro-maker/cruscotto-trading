# Cruscotto di trading per sessione — riepilogo per Claude Code

Documento di passaggio da una conversazione su claude.ai (29–30 ottobre 2026). Contiene il metodo di trading definito, le regole fisse, i risultati dei test, lo stato del cruscotto già costruito e i prossimi passi.

> Lettura tecnica a uso personale, non consulenza finanziaria.

---

## 1. Obiettivo

Uno strumento per fare trading **discrezionale a sessione**, basato solo sull'analisi del prezzo (price action), senza seguire le notizie.

- La strategia **non è meccanica**: si concretizza ogni volta che l'utente dà il via ("ragiona ora"). In quel momento si legge il mercato e si sceglie il setup del giorno, oppure si decide di non tradare.
- La **lettura cambia** a ogni sessione. Le **regole di rischio non cambiano mai**.
- Il metodo si valuta con un **diario delle sessioni** (20–30 sessioni), non con un backtest a regola fissa.

---

## 2. Il metodo

### Analisi top-down (sempre in quest'ordine)
1. **Settimanale:** la direzione di fondo (trend o correzione dentro un trend).
2. **Giornaliero:** il bias e le zone chiave (supporti, resistenze, Fibonacci).
3. **1h / 4h:** la struttura (massimi e minimi, range, rotture).
4. **15m:** solo per il timing di ingresso. Mai entrare sul 15m contro la struttura dell'1h.

### Setup ricorrenti
- **Retest della rottura:** il prezzo rompe un livello, torna a testarlo dall'altra parte e tiene. Si entra sul retest, non sulla rottura.
- **Pullback nel trend:** massimi crescenti sull'1h, ritorno su un supporto, candela di rifiuto sul 15m.
- **Presa di liquidità (sweep):** il prezzo buca un minimo evidente e rientra subito sopra.
- **Rottura del range:** chiusura 1h oltre il bordo del range e retest tenuto.
- **Nessun trade:** opzione legittima e da registrare.

### Orari utili
- Apertura europea (circa 9:00) e apertura di New York (circa 15:30 ora italiana).
- Oro, petrolio, valute: chiusi nel weekend; attenzione agli eventi in calendario (Fed/BCE, inflazione USA, scorte petrolio del mercoledì, OPEC, dati sul lavoro USA).

---

## 3. Regole fisse

| Regola | Valore |
|---|---|
| Rischio per trade | 1% del capitale (con 2.000 $ = 20 $) |
| Leva | massimo 2–3x, margine isolato |
| Rapporto rischio/rendimento | minimo 2:1, altrimenti non si entra |
| Stop | inserito insieme all'ordine, sempre |
| Stop giornaliero | dopo 2 perdite di fila o −2% (−2 R) |
| Gestione | metà profitto a TP1 (prima della resistenza), resto al target, stop a pareggio dopo TP1 |
| Ordini | preferire limit (maker): le commissioni decidono il risultato sui timeframe bassi |

**Dimensionamento:** si parte dal rischio in dollari, si divide per la distanza dello stop e si ottiene la quantità. La leva decide solo il margine impegnato, non il rischio.

```
rischio_usd = capitale × rischio%
quantità    = rischio_usd / |ingresso − stop|
nozionale   = quantità × ingresso
margine     = nozionale / leva
liq_long    ≈ ingresso × (1 − 1/leva)     (stima, senza margine di mantenimento)
```

Esempio: capitale 2.000 $, leva 2x. Se apri 4.000 $ di nozionale con stop a −22 $ su ETH perdi circa 40 $ = **2%**, il doppio della regola. Per restare all'1% la posizione va dimezzata (~0,9 ETH, ~2.000 $ di nozionale, ~1.000 $ di margine).

---

## 4. Contesto di mercato al 29/10/2026 (ETH)

- **Settimanale:** dopo il minimo a 1.308 € e l'accumulo 1.400–1.650 €, rally di agosto fino a ~2.500 €. Il calo di ottobre è una correzione sana (volumi bassi), sul Fibonacci 38,2% (~2.045 €).
- **Giornaliero (ETH/EUR):** supporto 2.040–2.100 € (minimo 2.037,14), resistenze 2.160–2.200 € e 2.280–2.300 €. RSI6 45 / RSI12 41 / RSI24 46, leggera divergenza rialzista.
- **Invalidazione settimanale:** chiusura sotto ~2.000 €; sotto 1.650 € la struttura di agosto salta.
- **1h (ETH/USDC):** range 2.290–2.390 $, resistenza testata tre volte.

### Sessione 1 — 29/10/2026, avvio 17:18
- Setup: **retest rottura, long**
- Ingresso limit **2.338** · stop **2.316** · TP1 **2.370** · target **2.385**
- Annulla se: chiusura 1h > 2.392, prezzo < 2.315 prima del riempimento, nessun riempimento entro le 22:00
- Esito osservato: minimo dopo l'analisi 2.345,68 alle 18:15 (non eseguito a quell'ora). Il 30/10 ETH era a ~2.240: **esito da verificare su OKX** (eseguito e stop, oppure annullato).

---

## 5. Backtest della versione meccanica (Keel)

Strategia salvata su Keel: **"ETH retest rottura 2R"** — `str_01m64aev48tcsysp70qtfaj9rk` (versioni v1 = 1h, v2 = 15m, v3 = 4h).

Regole testate: long sul ritorno nella zona di domanda (order block rialzista, `mitigation=close_through`) da cui è partita una rottura di struttura; filtro: chiusura giornaliera sopra la SMA50; stop sotto la zona; target 2R; rischio 1%, leva massima 2x. Capitale 4.000 $.

| Timeframe | Periodo | Commissioni | Rendimento | Drawdown max | Trade | Win rate |
|---|---|---|---|---|---|---|
| 1h | apr–ott 2026 | maker 2 bps | +9,5% | −11,8% | 50 | 42% |
| 1h | apr–ott 2026 | taker 4,5 bps | +0,5% | −13,4% | 50 | 42% |
| 1h | ago–ott 2026 | maker | −3,0% | −11,8% | 21 | 38% |
| 1h | set 2025–apr 2026 | maker | +1,2% | −17,6% | 35 | 47% |
| 15m | ago–ott 2026 | maker | −3,2% | −14,7% | 116 | 46% |
| 15m | ago–ott 2026 | taker | −26,0% | −26,7% | 116 | 39% |
| 4h | apr–ott 2026 | maker | −5,7% | −7,8% | 10 | 20% |
| 4h | set 2025–apr 2026 | maker | −7,9% | −10,1% | 9 | 22% |

**Conclusioni:**
- Vantaggio debole; il +9,5% viene quasi tutto dal rally apr–lug.
- Le commissioni decidono il risultato: usare ordini limit.
- Lo scalping sul 15m perde; il 4h da solo ha troppi pochi trade e stop troppo larghi.
- Idea da testare: **4h per direzione e zone, 1h per l'ingresso** (filtro di struttura 4h rialzista).
- Il motore esegue alla chiusura della candela, non sul livello limit: è un'approssimazione del metodo discrezionale, non il metodo stesso.

---

## 6. Il cruscotto già costruito

Pagina pubblicata come Artifact su claude.ai: **"Cruscotto di Sessione"** — https://claude.ai/artifact/JrrCwUzamNY8b5dBGs63gS (privata).

Sorgente: un unico file HTML (CSS e JS inline, nessuna libreria). Tema chiaro/scuro, layout a una colonna su telefono e due su desktop.

### Sezioni
1. **Prezzo live** del prodotto della sessione + prezzo inserito a mano come fallback.
2. **Piano del trade:** scala dei livelli in SVG (annulla sopra, target, TP1, zona di ingresso, stop, annulla sotto, prezzo attuale), riquadri dei livelli, R:R, distanza dall'ingresso, condizioni di annullamento, bias, pulsante "copia livelli".
3. **Stato ed esito:** 4 fasi (in attesa → in zona → aperta → chiusa); avvisi automatici (in zona, oltre le soglie di annullamento, TP1/target/stop raggiunti); pulsanti di esito che calcolano gli R; nota della sessione.
4. **Nuova sessione:** modulo con prodotto, direzione, setup, livelli, scadenza e bias, con validazione (per un long: stop < ingresso < target).
5. **Rischio e dimensione:** capitale, rischio %, leva → quantità, nozionale, margine, perdita allo stop, guadagno (metà a TP1, metà al target), liquidazione stimata.
6. **Regole fisse:** spunte verdi/rosse calcolate (rischio ≤ 1%, leva ≤ 3x, R:R ≥ 2, perdite di oggi < 2 e > −2 R).
7. **Diario:** sessioni, trade eseguiti, win rate, totale in R; elenco cliccabile.

### Dati e capacità
- **Prezzo:** connettore MCP *Coinversa Pulse*, tool `market_price` (`{symbol, useToonFormat:false}` → `{markPrice, indexPrice, change24h, fundingRate, openInterest, volume24h}` come stringhe). Prezzo mark di Hyperliquid, aggiornato ogni 60 s solo a pagina aperta (piano free: limite di richieste al minuto). `market_recent_candles` richiede il piano a pagamento.
- **Simboli:** `ETH`, `BTC`, `SOL`, `xyz:GOLD`, `xyz:BRENTOIL`, `xyz:EUR` (EUR/USD).
- **Archivio (db dell'Artifact):**
  - `config/settings` → `{capital, riskPct, leverage}` (ora 2000 / 1 / 2)
  - `sessions/<id>` → `{n, date "gg/mm/aaaa", startedAt, createdAt (ms), status: attesa|aperta|chiusa|annullata|notrade, symbol, side: long|short, setup, entry, stop, tp1, target, cancelAbove, cancelBelow, expiry, bias, note, filledAt?, closedAt?, exit?, r?}`
- **Limiti:** nessuna notifica push (solo avvisi a pagina aperta); per gli allarmi veri usare quelli di OKX: ≤ ingresso+zona, ≤ stop, ≥ TP1, ≥ rottura del range.

---

## 7. Prossimi passi possibili (per Claude Code)

1. **Versione locale o self-hosted** del cruscotto (es. Next.js o una pagina statica) con prezzo diretto dalle API pubbliche OKX (`/api/v5/market/candles`, `/api/v5/market/ticker`, WebSocket), candele vere e grafico.
2. **Notifiche reali:** un piccolo servizio che controlla i livelli e manda un avviso (Telegram, email o push) quando il prezzo entra in zona o tocca stop/target.
3. **Importazione/esportazione del diario** (CSV/JSON) e statistiche per prodotto e per setup.
4. **Backtest del filtro multi-timeframe** (4h direzione + 1h ingresso) e di una conferma all'ingresso (candela di rifiuto o sweep prima del retest).
5. **Integrazione con l'exchange** in sola lettura (posizioni e ordini aperti) per compilare l'esito in automatico. Niente esecuzione automatica degli ordini senza conferma.

### Note per chi riprende il lavoro
- Lingua: italiano.
- L'utente opera su OKX (ETH/USDC perp, leva configurabile) e usa anche Nexo; preferisce l'analisi del grafico al flusso di notizie.
- Capitale di riferimento per le operazioni a leva: 2.000–4.000 USDC.
