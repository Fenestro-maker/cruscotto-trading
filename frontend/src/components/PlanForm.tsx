import type { SetupKey, Side } from '@/api/client';
import { SETUP_LABELS } from '@/api/client';
import type { PlanFormState } from '@/lib/planForm';
import { Alert, Button, Card, Field, Input, Select, Textarea } from './ui';

export default function PlanForm({ value, onChange, problems, busy, editing, onSubmit, onCancel }: {
  value: PlanFormState;
  onChange: (v: PlanFormState) => void;
  problems: string[];
  busy: boolean;
  editing: boolean;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const set = <K extends keyof PlanFormState>(k: K, v: PlanFormState[K]) => onChange({ ...value, [k]: v });
  const numField = (k: 'entry' | 'stop' | 'tp1' | 'target' | 'cancel_above' | 'cancel_below', label: string, hint?: string) => (
    <Field label={label} hint={hint}>
      <Input inputMode="decimal" className="num" value={value[k]} onChange={(e) => set(k, e.target.value)} />
    </Field>
  );
  return (
    <Card title={editing ? 'Modifica il piano' : 'Piano della sessione'}>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); onSubmit(); }}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Direzione">
            <Select value={value.side} onChange={(e) => set('side', e.target.value as Side)}>
              <option value="long">Long</option>
              <option value="short">Short</option>
            </Select>
          </Field>
          <Field label="Setup">
            <Select value={value.setup} onChange={(e) => set('setup', e.target.value as SetupKey)}>
              {(Object.keys(SETUP_LABELS) as SetupKey[]).filter((k) => k !== 'nessuno').map((k) => (
                <option key={k} value={k}>{SETUP_LABELS[k]}</option>
              ))}
            </Select>
          </Field>
          {numField('entry', 'Ingresso (limit)')}
          {numField('stop', 'Stop')}
          {numField('tp1', 'TP1 (metà)')}
          {numField('target', 'Target')}
          {numField('cancel_above', 'Annulla sopra', 'Facoltativo')}
          {numField('cancel_below', 'Annulla sotto', 'Facoltativo')}
        </div>
        <Field label="Scadenza dell'ordine (ora italiana)" hint="Se non viene eseguito entro quest'ora, si annulla">
          <Input type="datetime-local" value={value.expiry} onChange={(e) => set('expiry', e.target.value)} />
        </Field>
        <Field label="Bias">
          <Input value={value.bias} onChange={(e) => set('bias', e.target.value)} />
        </Field>
        <Field label="Nota">
          <Textarea rows={2} value={value.note} onChange={(e) => set('note', e.target.value)} />
        </Field>
        {problems.length > 0 && <Alert tone="warn">{problems.join(' · ')}</Alert>}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={busy || problems.length > 0}>{editing ? 'Salva modifiche' : 'Avvia la sessione'}</Button>
          <Button type="button" variant="ghost" onClick={onCancel}>Annulla</Button>
        </div>
      </form>
    </Card>
  );
}
