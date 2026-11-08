import { BrowserRouter, Navigate, NavLink, Outlet, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { SignInPage } from "./pages/SignInPage";
import { MemosPage } from "./pages/MemosPage";
import { RecordPage } from "./pages/RecordPage";
import { MemoPage } from "./pages/MemoPage";
import { OnboardingPage } from "./pages/OnboardingPage";

function Shell() {
  const { me, signOut } = useAuth();
  if (!me) return <Navigate to="/sign-in" replace />;
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">M</span>
          <span className="brand-firm">{me.firm.name}</span>
        </div>
        <nav className="nav">
          <NavLink to="/memos">Memos</NavLink>
          <NavLink to="/record">Deal record</NavLink>
          <NavLink to="/onboarding">Firm setup</NavLink>
        </nav>
        <div className="who">
          <span className="avatar" title={me.email}>
            {me.name.slice(0, 1)}
          </span>
          <span className="who-email">{me.email}</span>
          <button className="link" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>
      <Outlet />
    </div>
  );
}

function Gate() {
  const { loading } = useAuth();
  if (loading) return <p className="center muted">Loading…</p>;
  return (
    <Routes>
      <Route path="/sign-in" element={<SignInPage />} />
      <Route element={<Shell />}>
        <Route path="/memos" element={<MemosPage />} />
        <Route path="/memos/:memoId" element={<MemoPage />} />
        <Route path="/record" element={<RecordPage />} />
        <Route path="/onboarding" element={<OnboardingPage />} />
        <Route path="*" element={<Navigate to="/memos" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <Gate />
      </AuthProvider>
    </BrowserRouter>
  );
}
