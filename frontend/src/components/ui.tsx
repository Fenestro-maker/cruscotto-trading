import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react';
import { cn } from '@/lib/format';

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn('rounded-xl border border-slate-800 bg-slate-900/60 p-4', className)}>
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost' | 'success';

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-sky-600 text-white hover:bg-sky-500',
  secondary: 'bg-slate-800 text-slate-100 hover:bg-slate-700 border border-slate-700',
  danger: 'bg-rose-700 text-white hover:bg-rose-600',
  success: 'bg-emerald-700 text-white hover:bg-emerald-600',
  ghost: 'text-slate-300 hover:bg-slate-800',
};

export function Button({ variant = 'primary', className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant }) {
  return (
    <button
      className={cn(
        'inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-sky-400',
        VARIANTS[variant], className,
      )}
      {...props}
    />
  );
}

const field = 'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-500 focus:outline-none';

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(field, className)} {...props} />;
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(field, className)} {...props} />;
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(field, className)} {...props} />;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-slate-400">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function Alert({ tone = 'info', children }: { tone?: 'info' | 'warn' | 'error' | 'ok'; children: ReactNode }) {
  const tones = {
    info: 'border-sky-800 bg-sky-950/50 text-sky-200',
    warn: 'border-amber-700 bg-amber-950/50 text-amber-200',
    error: 'border-rose-800 bg-rose-950/50 text-rose-200',
    ok: 'border-emerald-800 bg-emerald-950/50 text-emerald-200',
  };
  return <div role={tone === 'error' ? 'alert' : 'status'} className={cn('rounded-lg border px-3 py-2 text-sm', tones[tone])}>{children}</div>;
}

export function Stat({ label, value, tone }: { label: string; value: ReactNode; tone?: 'up' | 'down' }) {
  return (
    <div className="rounded-lg bg-slate-950/60 px-3 py-2">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={cn('num text-sm font-medium', tone === 'up' && 'text-emerald-400', tone === 'down' && 'text-rose-400')}>{value}</div>
    </div>
  );
}
