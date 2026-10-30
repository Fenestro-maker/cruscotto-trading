import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { errorMessage, settingsApi } from '@/api/client';
import type { Settings } from '@/api/client';
import { Alert, Button, Card, Field, Input, Textarea } from '@/components/ui';

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [capital, setCapital] = useState('');
  const [risk, setRisk] = useState('');
  const [leverage, setLeverage] = useState('');
  const [instruments, setInstruments] = useState('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  const fill = (s: Settings) => {
    setSettings(s);
    setCapital(String(s.capital));
    setRisk(String(s.risk_pct));
    setLeverage(String(s.leverage));
    setInstruments(s.instruments.join('\n'));
  };

  useEffect(() => {
    settingsApi.get().then(({ data }) => fill(data)).catch((err) => setError(errorMessage(err)));
  }, []);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    setSaved(false);
    try {
      const { data } = await settingsApi.update({
        capital: parseFloat(capital.replace(',', '.')),
        risk_pct: parseFloat(risk.replace(',', '.')),
        leverage: parseFloat(leverage.replace(',', '.')),
        instruments: instruments.split(/[\s,]+/).filter(Boolean),
      });
      fill(data);
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (!settings) return error ? <Alert tone="error">{error}</Alert> : <p className="text-sm text-slate-500">Caricamento…</p>;
  const r = settings.rules;

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card title="Conto e strumenti">
        <form onSubmit={save} className="space-y-3">
          <Field label="Capitale (USDC)">
            <Input inputMode="decimal" className="num" value={capital} onChange={(e) => setCapital(e.target.value)} />
          </Field>
          <Field label="Rischio per trade (%)" hint={`Massimo ${r.max_risk_pct}%: è una regola fissa`}>
            <Input inputMode="decimal" className="num" value={risk} onChange={(e) => setRisk(e.target.value)} />
          </Field>
          <Field label="Leva" hint={`Da 1 a ${r.max_leverage}x, margine isolato. Decide solo il margine, non il rischio`}>
            <Input inputMode="decimal" className="num" value={leverage} onChange={(e) => setLeverage(e.target.value)} />
          </Field>
          <Field label="Strumenti OKX (uno per riga)" hint="Codici OKX, es. ETH-USDC-SWAP, BTC-USDT-SWAP">
            <Textarea rows={4} className="num" value={instruments} onChange={(e) => setInstruments(e.target.value)} />
          </Field>
          {error && <Alert tone="error">{error}</Alert>}
          {saved && <Alert tone="ok">Impostazioni salvate</Alert>}
          <Button type="submit" disabled={busy}>Salva</Button>
        </form>
      </Card>
      <div className="space-y-4">
        <Card title="Regole fisse">
          <ul className="list-disc space-y-1 pl-5 text-sm text-slate-300">
            <li>Rischio massimo {r.max_risk_pct}% del capitale per trade</li>
            <li>Leva massima {r.max_leverage}x, margine isolato</li>
            <li>Rapporto rischio/rendimento minimo {r.min_rr}:1, altrimenti non si entra</li>
            <li>Stop inserito insieme all'ordine, sempre</li>
            <li>Stop giornaliero dopo {r.daily_max_losses_in_row} perdite di fila o {r.daily_max_loss_r} R</li>
            <li>Metà a TP1, resto al target, stop a pareggio dopo TP1</li>
            <li>Ordini limit (maker)</li>
          </ul>
          <p className="mt-3 text-xs text-slate-500">Le regole sono scritte nel server e non si cambiano da qui.</p>
        </Card>
        <Card title="Analisi con Claude">
          {settings.ai_configured
            ? <p className="text-sm text-slate-300">Attiva · modello <span className="num">{settings.ai_model}</span></p>
            : <Alert tone="warn">Non attiva: manca ANTHROPIC_API_KEY nel file backend/.env del server.</Alert>}
        </Card>
        <Card title="OKX">
          <p className="text-sm text-slate-300">Dati di mercato da <span className="num">{settings.okx_base_url}</span></p>
          <p className="mt-1 text-sm text-slate-300">
            Conto: {settings.okx_account ? 'collegato in sola lettura' : 'non collegato (manca la chiave API in backend/.env)'}
          </p>
          <p className="mt-3 text-xs text-slate-500">Nessun ordine viene inviato a OKX da questa piattaforma.</p>
        </Card>
      </div>
    </div>
  );
}
