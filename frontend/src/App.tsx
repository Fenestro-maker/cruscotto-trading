import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import type { ReactNode } from 'react';
import { AuthProvider, useAuth } from '@/context/AuthContext';
import Layout from '@/components/Layout';
import LoginPage from '@/pages/LoginPage';
import SessionPage from '@/pages/SessionPage';
import JournalPage from '@/pages/JournalPage';
import SettingsPage from '@/pages/SettingsPage';

function Protected({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return user ? <Layout>{children}</Layout> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<Protected><SessionPage /></Protected>} />
          <Route path="/diario" element={<Protected><JournalPage /></Protected>} />
          <Route path="/impostazioni" element={<Protected><SettingsPage /></Protected>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
