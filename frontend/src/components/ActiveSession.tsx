import { useState } from 'react';
import type { Outcome, Session } from '@/api/client';
import { SETUP_LABELS } from '@/api/client';
import { cn, fmtDateTime, fmtPct, fmtPrice, instLabel } from '@/lib/format';
import { Alert, Button, Card, Field, Input, Stat } from './ui';

const STEPS = [
  { key: 'attesa', label: 'In attesa' },
  { key: 'zona', label: 'In zona' },
  { key: 'aperta', label: 'Aperta' },
  { key: 'chiusa', label: 'Chiusa' },
];

// Distanza dall'ingresso entro cui il prezzo è "in zona"
const ZONE_PCT = 0.2;

interface Alarm { tone: 'info' | 'warn' | 'error' | 'ok'; text: string }

function alarms(s: Session, price: number | null): Alarm[] {
  if (price == null || s.entry == null || s.stop == null || s.tp1 == null || s.target == null) return [];
  const long = s.side === 'long';
  const beyond = (level: number) => (long ? price >= level : price <= level);
  const below = (level: number) => (long ? price <= level : price >= level);
  const out: Alarm[] = [];
  if (s.status === 'attesa') {
    if (Math.abs(price - s.entry) / s.entry * 100 <= ZONE_PCT) out.push({ tone: 'info', text: 'Prezzo in zona di ingresso: controlla il 15m (candela di rifiuto) e che l\'ordine limit sia inserito.' });
    if (s.cancel_above != null && price > s.cancel_above) out.push({ tone: 'warn', text: `Prezzo sopra ${fmtPrice(s.cancel_above)}: condizione di annullamento.` });
    if (s.cancel_below != null && price < s.cancel_below) out.push({ tone: 'warn', text: `Prezzo sotto ${fmtPrice(s.cancel_below)}: condizione di annullamento.` });
    if (s.expiry && Date.now() > new Date(s.expiry).getTime()) out.push({ tone: 'warn', text: 'Scadenza passata senza esecuzione: annulla l\'ordine su OKX.' });
  }
  if (s.status === 'aperta') {
    if (below(s.stop)) out.push({ tone: 'error', text: 'Prezzo allo stop.' });
    else if (beyond(s.target)) out.push({ tone: 'ok', text: 'Target raggiunto.' });
    else if (beyond(s.tp1)) out.push({ tone: 'ok', text: 'TP1 raggiunto: chiudi metà e sposta lo stop a pareggio.' });
  }
  return out;
}

export default function ActiveSession({ session, price, busy, onFill, onCancel, onClose, onEdit }: {
  session: Session;
  price: number | null;
  busy: boolean;
  onFill: () => void;
  onCancel: (reason: string) => void;
  onClose: (outcome: Outcome, exitPrice?: number, tp1Taken?: boolean) => void;
  onEdit: () => void;
}) {
  const [manual, setManual] = useState(false);
  const [exitPrice, setExitPrice] = useState('');
  const [tp1Taken, setTp1Taken] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [reason, setReason] = useState('');

  const inZone = session.status === 'attesa' && price != null && session.entry != null
    && Math.abs(price - session.entry) / session.entry * 100 <= ZONE_PCT;
  const step = session.status === 'aperta' ? 2 : inZone ? 1 : 0;
  const dist = price != null && session.entry != null ? ((price - session.entry) / session.entry) * 100 : null;
  const list = alarms(session, price);
  const exitNum = parseFloat(exitPrice.replace(',', '.'));

  return (
    <Card
      title={`Sessione ${session.n} · ${instLabel(session.instrument)}`}
      actions={<span className="text-xs text-slate-500">dal {fmtDateTime(session.created_at)}</span>}
    >
      <div className="space-y-4">
        <ol className="grid grid-cols-4 gap-1" aria-label="Fase della sessione">
          {STEPS.map((s, i) => (
            <li key={s.key} aria-current={i === step ? 'step' : undefined}
              className={cn('rounded-md px-2 py-1.5 text-center text-xs font-medium',
                i === step ? 'bg-sky-700 text-white' : i < step ? 'bg-slate-700 text-slate-200' : 'bg-slate-900 text-slate-500')}>
              {s.label}
            </li>
          ))}
        </ol>

        <div className="flex flex-wrap items-center gap-2">
          <span className={cn('rounded-full px-2.5 py-1 text-xs font-semibold', session.side === 'long' ? 'bg-emerald-900 text-emerald-200' : 'bg-rose-900 text-rose-200')}>
            {session.side.toUpperCase()} · {SETUP_LABELS[session.setup]}
          </span>
          {session.rr != null && <span className="num rounded-full bg-slate-800 px-2.5 py-1 text-xs">R:R {session.rr}:1</span>}
          {dist != null && session.status === 'attesa' && <span className="num text-xs text-slate-400">prezzo {fmtPct(dist)} dall'ingresso</span>}
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="Ingresso" value={fmtPrice(session.entry)} />
          <Stat label="Stop" value={fmtPrice(session.stop)} tone="down" />
          <Stat label="TP1" value={fmtPrice(session.tp1)} tone="up" />
          <Stat label="Target" value={fmtPrice(session.target)} tone="up" />
          {session.cancel_above != null && <Stat label="Annulla sopra" value={fmtPrice(session.cancel_above)} />}
          {session.cancel_below != null && <Stat label="Annulla sotto" value={fmtPrice(session.cancel_below)} />}
          {session.expiry && <Stat label="Scadenza" value={fmtDateTime(session.expiry)} />}
        </div>
        {session.bias && <p className="text-sm text-slate-300"><span className="text-slate-500">Bias: </span>{session.bias}</p>}

        {list.map((a, i) => <Alert key={i} tone={a.tone}>{a.text}</Alert>)}

        {session.status === 'attesa' && !cancelling && (
          <div className="flex flex-wrap gap-2">
            <Button variant="success" onClick={onFill} disabled={busy}>Ordine eseguito</Button>
            <Button variant="secondary" onClick={onEdit} disabled={busy}>Modifica</Button>
            <Button variant="ghost" onClick={() => setCancelling(true)} disabled={busy}>Annulla l'ordine</Button>
          </div>
        )}
        {cancelling && (
          <div className="space-y-2">
            <Field label="Motivo dell'annullamento">
              <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="es. chiusura 1h sopra la soglia" />
            </Field>
            <div className="flex gap-2">
              <Button variant="danger" onClick={() => onCancel(reason)} disabled={busy}>Conferma annullamento</Button>
              <Button variant="ghost" onClick={() => setCancelling(false)}>Indietro</Button>
            </div>
          </div>
        )}

        {session.status === 'aperta' && (
          <div className="space-y-2">
            <p className="text-xs text-slate-500">Eseguito il {fmtDateTime(session.filled_at)}. Come si è chiusa?</p>
            <div className="grid grid-cols-2 gap-2">
              <Button variant="danger" onClick={() => onClose('stop')} disabled={busy}>Stop (−1 R)</Button>
              <Button variant="secondary" onClick={() => onClose('tp1_pareggio')} disabled={busy}>TP1, poi pareggio</Button>
              <Button variant="success" onClick={() => onClose('tp1_target')} disabled={busy}>TP1 + target</Button>
              <Button variant="secondary" onClick={() => setManual((m) => !m)} disabled={busy}>Uscita manuale</Button>
            </div>
            {manual && (
              <div className="space-y-2 rounded-lg border border-slate-800 p-3">
                <Field label="Prezzo di uscita">
                  <Input inputMode="decimal" className="num" value={exitPrice} onChange={(e) => setExitPrice(e.target.value)} />
                </Field>
                <label className="flex items-center gap-2 text-sm text-slate-300">
                  <input type="checkbox" checked={tp1Taken} onChange={(e) => setTp1Taken(e.target.checked)} className="h-4 w-4" />
                  Metà posizione già chiusa a TP1
                </label>
                <Button onClick={() => onClose('manuale', exitNum, tp1Taken)} disabled={busy || !Number.isFinite(exitNum)}>Registra l'uscita</Button>
              </div>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}
