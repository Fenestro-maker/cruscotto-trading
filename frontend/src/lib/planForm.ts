import type { Analysis, PlanInput, SetupKey, Side } from '@/api/client';
import { toLocalInput } from '@/lib/format';

/** Stato del modulo: i numeri restano stringhe finché si scrive */
export interface PlanFormState {
  side: Side;
  setup: SetupKey;
  entry: string;
  stop: string;
  tp1: string;
  target: string;
  cancel_above: string;
  cancel_below: string;
  expiry: string; // datetime-local, ora italiana
  bias: string;
  note: string;
  analysis_id: string | null;
}

export const EMPTY_FORM: PlanFormState = {
  side: 'long', setup: 'retest_rottura', entry: '', stop: '', tp1: '', target: '',
  cancel_above: '', cancel_below: '', expiry: '', bias: '', note: '', analysis_id: null,
};

const str = (v: number | null | undefined) => (v == null ? '' : String(v));

export function formFromAnalysis(a: Analysis): PlanFormState {
  const r = a.result;
  return {
    side: r.side === 'none' ? 'long' : r.side,
    setup: r.setup === 'nessuno' ? 'retest_rottura' : r.setup,
    entry: str(r.entry), stop: str(r.stop), tp1: str(r.tp1), target: str(r.target),
    cancel_above: str(r.cancel_above), cancel_below: str(r.cancel_below),
    expiry: toLocalInput(a.expiry),
    bias: r.bias,
    note: '',
    analysis_id: a.id,
  };
}

export function formFromPlan(p: PlanInput): PlanFormState {
  return {
    side: p.side, setup: p.setup,
    entry: str(p.entry), stop: str(p.stop), tp1: str(p.tp1), target: str(p.target),
    cancel_above: str(p.cancel_above), cancel_below: str(p.cancel_below),
    expiry: toLocalInput(p.expiry), bias: p.bias, note: p.note, analysis_id: p.analysis_id,
  };
}

const num = (s: string): number | null => {
  const v = parseFloat(s.replace(',', '.'));
  return Number.isFinite(v) ? v : null;
};

export function planFromForm(f: PlanFormState, instrument: string): PlanInput {
  return {
    instrument,
    side: f.side,
    setup: f.setup,
    entry: num(f.entry), stop: num(f.stop), tp1: num(f.tp1), target: num(f.target),
    cancel_above: num(f.cancel_above), cancel_below: num(f.cancel_below),
    expiry: f.expiry || null,
    bias: f.bias, note: f.note, analysis_id: f.analysis_id,
  };
}
