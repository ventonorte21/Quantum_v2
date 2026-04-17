import { useState, useEffect, useCallback } from "react";
import { Routes, Route, useLocation, useNavigate } from "react-router-dom";
import "@/App.css";
import axios from "axios";
import {
  ChartLineUp,
  Lightning, Warning,
  XCircle, Pulse,
  SignOut, User
} from "@phosphor-icons/react";
import { LoginPage, AuthCallback } from "./components/AuthPages";
import ScalpDashboard from "./components/ScalpDashboard";
import ScalpAutoTunePanel from "./components/ScalpAutoTunePanel";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "";
const API = `${BACKEND_URL}/api`;

// Inject Bearer token from localStorage into every axios request
axios.interceptors.request.use((config) => {
  const token = localStorage.getItem("session_token");
  if (token) {
    config.headers = config.headers || {};
    config.headers["Authorization"] = `Bearer ${token}`;
  }
  return config;
});

// Helper to build fetch options with Bearer token
function authFetchOptions(base = {}) {
  const token = localStorage.getItem("session_token");
  const headers = { ...(base.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return { ...base, credentials: "include", headers };
}

// ProtectedRoute — checks auth via /api/auth/me
function ProtectedRoute({ children }) {
  const [authState, setAuthState] = useState(null); // null=checking, true/false
  const [user, setUser] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    // CRITICAL: If returning from OAuth callback, skip the /me check.
    // AuthCallback will exchange the session_id and establish the session first.
    if (window.location.hash?.includes('session_id=')) {
      setAuthState(false);
      return;
    }

    const checkAuth = async () => {
      try {
        const resp = await fetch(`${API}/auth/me`, authFetchOptions());
        if (resp.ok) {
          const data = await resp.json();
          setUser(data);
          setAuthState(true);
        } else {
          // Also try cached user info from localStorage before giving up
          const cached = localStorage.getItem("user_info");
          const token = localStorage.getItem("session_token");
          if (cached && token) {
            setUser(JSON.parse(cached));
            setAuthState(true);
          } else {
            setAuthState(false);
            navigate("/login", { replace: true });
          }
        }
      } catch {
        setAuthState(false);
        navigate("/login", { replace: true });
      }
    };
    checkAuth();
  }, [navigate]);

  if (authState === null) {
    return (
      <div className="min-h-screen bg-[#09090B] flex items-center justify-center">
        <div className="flex items-center gap-3">
          <div className="w-5 h-5 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-zinc-400">Verificando sessao...</span>
        </div>
      </div>
    );
  }

  if (!authState) return null;

  return children({ user });
}

// Dashboard — the main trading terminal (all existing App content)
function Dashboard({ user }) {
  const [error, setError] = useState(null);
  const [tradingStatus, setTradingStatus] = useState(null);
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [schedulerActive, setSchedulerActive] = useState(false);
  const [activePanel, setActivePanel] = useState(null); // 'replay' | 'scalp' | null
  const [scalpSubPanel, setScalpSubPanel] = useState(null); // 'replay' | null

  const navigate = useNavigate();

  const handleLogout = async () => {
    try {
      await fetch(`${API}/auth/logout`, authFetchOptions({ method: "POST" }));
    } catch { /* ignore */ }
    localStorage.removeItem("session_token");
    localStorage.removeItem("user_info");
    navigate("/login", { replace: true });
  };

  // === API Fetchers ===

  const fetchSchedulerStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/replay/schedule`, { credentials: "include" });
      if (r.ok) {
        const data = await r.json();
        setSchedulerActive(data.active === true && data.auto_apply === true);
      }
    } catch { /* ignore */ }
  }, []);

  const flattenAllScalpTrades = async (reason = 'manual') => {
    try {
      await axios.post(`${API}/scalp/trades/flatten_all?reason=${reason}`);
    } catch (e) { setError(`Scalp flatten failed: ${e?.response?.data?.detail || e.message}`); }
  };

  // === Effects ===
  useEffect(() => {
    fetchSchedulerStatus();
  }, [fetchSchedulerStatus]);

  // Session-Aware Calendar Polling (RTH: 60s | Globex: 120s | Halted: 5min)
  useEffect(() => {
    let pollTimer = null;
    let destroyed = false;
    const pollCycle = async () => {
      if (destroyed) return;
      try {
        const res = await axios.get(`${API}/trading-calendar/status`, { timeout: 5000 });
        if (!destroyed) setTradingStatus(res.data);
        const session = res.data?.session || res.data || {};
        const cmeSession = session?.cme_session || 'globex';
        const isHalted = session?.is_cme_halted || false;
        const isWeekend = session?.is_weekend || false;
        const interval = (isHalted || isWeekend) ? 300000 : cmeSession === 'rth' ? 60000 : 120000;
        if (!destroyed) pollTimer = setTimeout(pollCycle, interval);
      } catch {
        if (!destroyed) pollTimer = setTimeout(pollCycle, 30000);
      }
    };
    pollCycle();
    return () => { destroyed = true; if (pollTimer) clearTimeout(pollTimer); };
  }, []);

  // Auto-dismiss error toast after 8s
  useEffect(() => {
    if (error) {
      const timer = setTimeout(() => setError(null), 8000);
      return () => clearTimeout(timer);
    }
  }, [error]);

  return (
    <div className="min-h-screen bg-[#09090B] text-zinc-100">
      {/* Header */}
      <header className="border-b border-zinc-800/40 bg-[#09090B]/95 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-[1920px] mx-auto px-6 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <ChartLineUp size={24} weight="bold" className="text-blue-400" />
                <h1 className="text-lg font-bold tracking-tight font-['Chivo']">QUANTUM TRADING SCALP</h1>
              </div>
              <span className="text-[10px] font-mono text-zinc-600 border border-zinc-800/60 px-2 py-0.5">MNQ · MES</span>
            </div>

            <div className="flex items-center gap-3">
              <button
                data-testid="autotune-toggle-btn"
                onClick={() => setActivePanel(activePanel === 'replay' ? null : 'replay')}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold transition-all duration-150 border ${
                  schedulerActive
                    ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-400'
                    : activePanel === 'replay'
                      ? 'bg-blue-500/20 border-blue-500/50 text-blue-400'
                      : 'border-zinc-700 text-zinc-400 hover:border-zinc-600'
                }`}
              >
                <ChartLineUp size={14} weight="bold" />
                <span>Auto Tune</span>
                {schedulerActive && <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />}
              </button>
              <div className="flex items-center gap-2 text-xs text-zinc-500">
                <Pulse size={14} className="text-emerald-400 animate-pulse" />
                <span className="font-mono">LIVE</span>
              </div>
              <div className="text-xs text-zinc-500 font-mono">{new Date().toLocaleTimeString()}</div>

              {/* User Menu */}
              <div className="relative">
                <button
                  data-testid="user-menu-btn"
                  onClick={() => setShowUserMenu(!showUserMenu)}
                  className="flex items-center gap-2 px-2 py-1.5 border border-zinc-800 hover:border-zinc-700 transition-all"
                >
                  {user?.picture ? (
                    <img src={user.picture} alt="" className="w-5 h-5 rounded-full" />
                  ) : (
                    <User size={14} className="text-zinc-400" />
                  )}
                  <span className="text-[10px] text-zinc-400 font-mono max-w-[100px] truncate">{user?.name || user?.email}</span>
                </button>
                {showUserMenu && (
                  <div className="absolute right-0 top-full mt-1 w-48 bg-[#111113] border border-zinc-800 shadow-xl z-50" data-testid="user-dropdown">
                    <div className="p-3 border-b border-zinc-800/60">
                      <div className="text-xs font-semibold text-zinc-200 truncate">{user?.name}</div>
                      <div className="text-[10px] text-zinc-500 truncate">{user?.email}</div>
                    </div>
                    <button
                      data-testid="logout-btn"
                      onClick={handleLogout}
                      className="w-full flex items-center gap-2 px-3 py-2.5 text-xs text-red-400 hover:bg-red-500/10 transition-all"
                    >
                      <SignOut size={14} />
                      Sair
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-[1920px] mx-auto">
        <main>
          <ScalpDashboard tradingStatus={tradingStatus} onFlattenAll={flattenAllScalpTrades} />
        </main>
      </div>

      {/* Error Toast — auto-dismiss after 8s */}
      {error && (
        <div className="fixed bottom-4 right-4 bg-red-500/10 border border-red-500/30 p-4 flex items-center gap-3 z-50 animate-fade-in">
          <Warning size={20} className="text-red-400" />
          <span className="text-sm text-red-400">{error}</span>
          <button onClick={() => setError(null)} className="text-red-400 hover:text-red-300"><XCircle size={18} /></button>
        </div>
      )}

      {/* Auto Tune Panel (overlay — keeps Dashboard alive) */}
      {activePanel === 'replay' && (
        <div className="fixed inset-0 bg-[#09090B] z-50 overflow-auto" data-testid="replay-panel">
          <ScalpAutoTunePanel onClose={() => setActivePanel(null)} />
        </div>
      )}

      {/* Scalp Engine Panel (overlay — keeps Dashboard alive) */}
      {activePanel === 'scalp' && (
        <div className="fixed inset-0 bg-[#09090B] z-50 overflow-auto" data-testid="scalp-panel">
          <div className="border-b border-zinc-800/40 bg-[#09090B]/95 backdrop-blur-sm sticky top-0 z-10">
            <div className="max-w-[1920px] mx-auto px-6 py-3 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Lightning size={18} weight="fill" className="text-yellow-400" />
                <span className="text-sm font-bold text-zinc-200 uppercase tracking-wider">Scalp Engine</span>
                <span className="text-[10px] text-zinc-600 font-mono">S1 / S2 / S3 — 1–3 min</span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setScalpSubPanel(scalpSubPanel === 'replay' ? null : 'replay')}
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold transition-all duration-150 border ${
                    schedulerActive
                      ? 'bg-emerald-500/20 border-emerald-500/50 text-emerald-400'
                      : scalpSubPanel === 'replay'
                        ? 'bg-blue-500/20 border-blue-500/50 text-blue-400'
                        : 'border-zinc-700 text-zinc-400 hover:border-zinc-600'
                  }`}
                >
                  <ChartLineUp size={14} weight="bold" />
                  <span>Auto Tune</span>
                  {schedulerActive && <div className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />}
                </button>
                <button
                  onClick={() => { setActivePanel(null); setScalpSubPanel(null); }}
                  className="text-xs text-zinc-400 border border-zinc-700 px-3 py-1.5 hover:border-zinc-500 transition-all"
                >
                  ← Voltar
                </button>
              </div>
            </div>
          </div>
          <ScalpDashboard tradingStatus={tradingStatus} onFlattenAll={flattenAllScalpTrades} />

          {/* Scalp sub-panels — rendered as full-screen overlays above the scalp panel */}
          {scalpSubPanel === 'replay' && (
            <div className="fixed inset-0 bg-[#09090B] z-[60] overflow-auto" data-testid="scalp-replay-panel">
              <ScalpAutoTunePanel onClose={() => setScalpSubPanel(null)} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// BackendReadinessGate — polls /api/health until backend signals ready=true.
// During the ~45s warm-up window after a cold start the backend returns
// { ready: false } — this gate prevents login attempts and 504s by showing
// a friendly loading screen and retrying automatically with backoff.
function BackendReadinessGate({ children }) {
  const [ready, setReady] = useState(null); // null=checking, true=ready, false=failed
  const [dots, setDots] = useState('');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let dotTimer = null;

    const animateDots = () => {
      dotTimer = setInterval(() => {
        if (!cancelled) setDots(d => d.length >= 3 ? '' : d + '.');
      }, 500);
    };

    const check = async (retryCount) => {
      try {
        const resp = await fetch(`${API}/health`, { cache: 'no-store' });
        if (!cancelled && resp.ok) {
          const data = await resp.json();
          if (data.ready === true) {
            setReady(true);
            return;
          }
        }
      } catch (_) { /* network not up yet — keep retrying */ }

      if (cancelled) return;

      // Back-off: 3s, 4s, 5s, 6s, … up to 10s max per retry
      const delay = Math.min(3000 + retryCount * 1000, 10000);
      setAttempt(retryCount + 1);
      setTimeout(() => { if (!cancelled) check(retryCount + 1); }, delay);
    };

    animateDots();
    check(0);

    return () => {
      cancelled = true;
      clearInterval(dotTimer);
    };
  }, []);

  if (ready === true) return children;

  return (
    <div className="min-h-screen bg-[#09090B] flex flex-col items-center justify-center gap-4">
      <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
      <div className="text-center">
        <p className="text-sm text-zinc-300 font-medium">Iniciando backend{dots}</p>
        {attempt > 3 && (
          <p className="text-xs text-zinc-500 mt-1">
            Carregando dados de mercado ({attempt} tentativas)
          </p>
        )}
      </div>
    </div>
  );
}

// Main App — routing
function AppRouter() {
  const location = useLocation();

  // CRITICAL: Check URL fragment for session_id synchronously during render
  // This prevents race conditions — useEffect would be too late
  if (location.hash?.includes('session_id=')) {
    return <AuthCallback />;
  }

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/replay" element={
        <ProtectedRoute>
          {() => <ScalpAutoTunePanel onClose={() => window.history.back()} />}
        </ProtectedRoute>
      } />
      <Route path="/dashboard" element={
        <ProtectedRoute>
          {({ user }) => <Dashboard user={user} />}
        </ProtectedRoute>
      } />
      <Route path="*" element={
        <ProtectedRoute>
          {({ user }) => <Dashboard user={user} />}
        </ProtectedRoute>
      } />
    </Routes>
  );
}

export default function App() {
  return (
    <BackendReadinessGate>
      <AppRouter />
    </BackendReadinessGate>
  );
}
