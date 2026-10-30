import clsx, { type ClassValue } from 'clsx';

export const cn = (...inputs: ClassValue[]) => clsx(inputs);

export function fmtPrice(x: number | null | undefined): string {
  if (x == null || Number.isNaN(x)) return '—';
  const digits = Math.abs(x) >= 100 ? 2 : Math.abs(x) >= 1 ? 4 : 6;
  return x.toLocaleString('it-IT', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtNum(x: number | null | undefined, digits = 2): string {
  if (x == null || Number.isNaN(x)) return '—';
  return x.toLocaleString('it-IT', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtUsd(x: number | null | undefined): string {
  if (x == null || Number.isNaN(x)) return '—';
  return `${x < 0 ? '−' : ''}${Math.abs(x).toLocaleString('it-IT', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} $`;
}

export function fmtR(r: number | null | undefined): string {
  if (r == null) return '—';
  return `${r > 0 ? '+' : r < 0 ? '−' : ''}${Math.abs(r).toLocaleString('it-IT', { maximumFractionDigits: 2 })} R`;
}

export function fmtPct(x: number, digits = 2): string {
  return `${x > 0 ? '+' : ''}${x.toLocaleString('it-IT', { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
}

const DT = new Intl.DateTimeFormat('it-IT', {
  timeZone: 'Europe/Rome', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
});

export function fmtDateTime(iso: string | null | undefined): string {
  return iso ? DT.format(new Date(iso)) : '—';
}

/** Valore per <input type="datetime-local"> in ora italiana */
export function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return '';
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Europe/Rome', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
    }).formatToParts(new Date(iso)).map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
