import type { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { BookOpen, LineChart, LogOut, Settings } from 'lucide-react';
import { useAuth } from '@/context/AuthContext';
import { cn } from '@/lib/format';

const LINKS = [
  { to: '/', label: 'Sessione', icon: LineChart },
  { to: '/diario', label: 'Diario', icon: BookOpen },
  { to: '/impostazioni', label: 'Impostazioni', icon: Settings },
];

export default function Layout({ children }: { children: ReactNode }) {
  const { logout } = useAuth();
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-2 px-4 py-2">
          <span className="mr-2 hidden font-semibold text-slate-100 sm:block">Cruscotto di sessione</span>
          <nav className="flex flex-1 gap-1">
            {LINKS.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                end
                className={({ isActive }) => cn(
                  'flex min-h-10 items-center gap-2 rounded-lg px-3 text-sm',
                  isActive ? 'bg-slate-800 text-white' : 'text-slate-400 hover:text-slate-100',
                )}
              >
                <Icon className="h-4 w-4" aria-hidden />
                <span className={cn(to !== '/' && 'hidden sm:inline')}>{label}</span>
              </NavLink>
            ))}
          </nav>
          <button onClick={logout} className="flex min-h-10 items-center gap-2 rounded-lg px-3 text-sm text-slate-400 hover:text-slate-100" aria-label="Esci">
            <LogOut className="h-4 w-4" aria-hidden />
            <span className="hidden sm:inline">Esci</span>
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-4">{children}</main>
      <footer className="mx-auto max-w-7xl px-4 pb-6 text-xs text-slate-600">
        Lettura tecnica a uso personale, non consulenza finanziaria. Nessun ordine viene inviato a OKX da qui.
      </footer>
    </div>
  );
}
