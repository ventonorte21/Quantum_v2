import { useState, useEffect, useRef } from "react";
import { ShieldCheck, Key } from "@phosphor-icons/react";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "";
const API = `${BACKEND_URL}/api`;

const isInIframe = (() => { try { return window.self !== window.top; } catch { return true; } })();

export function LoginPage() {
  const [checking, setChecking] = useState(true);
  const [waitingPopup, setWaitingPopup] = useState(false);
  const [passcode, setPasscode] = useState("");
  const [passcodeLoading, setPasscodeLoading] = useState(false);
  const [passcodeError, setPasscodeError] = useState(null);
  const popupRef = useRef(null);
  const pollRef = useRef(null);

  useEffect(() => {
    const checkAuth = async () => {
      try {
        const token = localStorage.getItem("session_token");
        const headers = {};
        if (token) headers["Authorization"] = `Bearer ${token}`;
        const resp = await fetch(`${API}/auth/me`, { credentials: "include", headers });
        if (resp.ok) {
          window.location.href = "/dashboard";
          return;
        }
      } catch (e) {}
      setChecking(false);
    };
    checkAuth();
  }, []);

  // Listen for localStorage token set by the auth popup
  useEffect(() => {
    const onStorage = (e) => {
      if (e.key === "session_token" && e.newValue) {
        clearInterval(pollRef.current);
        if (popupRef.current && !popupRef.current.closed) popupRef.current.close();
        window.location.href = "/dashboard";
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const handleGoogleLogin = () => {
    setPasscodeError(null);
    const redirectUrl = window.location.origin + "/dashboard";
    const authUrl = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;

    const popup = window.open(authUrl, "quantum_auth", "width=520,height=640,left=200,top=100,toolbar=no,menubar=no,scrollbars=yes");

    if (!popup || popup.closed || typeof popup.closed === "undefined") {
      window.location.href = authUrl;
      return;
    }

    popupRef.current = popup;
    setWaitingPopup(true);

    pollRef.current = setInterval(() => {
      if (popup.closed) {
        clearInterval(pollRef.current);
        const token = localStorage.getItem("session_token");
        if (token) {
          window.location.href = "/dashboard";
        } else {
          setWaitingPopup(false);
          setPasscodeError("Login cancelado ou falhou. Tente o código de acesso abaixo.");
        }
      }
    }, 500);
  };

  const handlePasscodeLogin = async (e) => {
    e.preventDefault();
    if (!passcode.trim()) return;
    setPasscodeLoading(true);
    setPasscodeError(null);
    try {
      const resp = await fetch(`${API}/auth/passcode/login?passcode=${encodeURIComponent(passcode.trim())}`, {
        credentials: "include",
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        setPasscodeError(data.detail || "Código inválido");
        setPasscodeLoading(false);
        return;
      }
      if (data.session_token) {
        localStorage.setItem("session_token", data.session_token);
        localStorage.setItem("user_info", JSON.stringify({
          user_id: data.user_id,
          email: data.email,
          name: data.name,
          picture: data.picture,
        }));
      }
      window.location.href = "/dashboard";
    } catch (err) {
      setPasscodeError("Erro de conexão com o servidor");
      setPasscodeLoading(false);
    }
  };

  if (checking) {
    return (
      <div className="min-h-screen bg-[#09090B] flex items-center justify-center">
        <div className="w-5 h-5 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (waitingPopup) {
    return (
      <div className="min-h-screen bg-[#09090B] flex items-center justify-center" data-testid="login-page">
        <div className="w-full max-w-sm mx-auto px-6">
          <div className="border border-zinc-800/60 bg-zinc-900/30 p-8 text-center">
            <div className="w-12 h-12 border border-zinc-700 flex items-center justify-center mb-6 mx-auto">
              <ShieldCheck size={28} weight="bold" className="text-blue-400" />
            </div>
            <div className="flex items-center justify-center gap-3 mb-4">
              <div className="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
              <span className="text-sm text-zinc-300">Aguardando autenticação...</span>
            </div>
            <p className="text-[11px] text-zinc-500 mb-5">
              Complete o login Google na janela que abriu.
            </p>
            <button
              onClick={() => {
                clearInterval(pollRef.current);
                if (popupRef.current && !popupRef.current.closed) popupRef.current.close();
                setWaitingPopup(false);
              }}
              className="text-[11px] text-zinc-600 hover:text-zinc-400 underline"
            >
              Cancelar
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#09090B] flex items-center justify-center" data-testid="login-page">
      <div className="w-full max-w-sm mx-auto px-6">
        <div className="border border-zinc-800/60 bg-zinc-900/30 p-8">
          <div className="flex flex-col items-center mb-8">
            <div className="w-12 h-12 border border-zinc-700 flex items-center justify-center mb-4">
              <ShieldCheck size={28} weight="bold" className="text-blue-400" />
            </div>
            <h1 className="text-lg font-bold tracking-tight font-['Chivo'] text-zinc-100">QUANTUM TRADING SCALP</h1>
            <span className="text-[10px] text-zinc-600 font-mono mt-1">V3 TERMINAL</span>
          </div>

          <div className="bg-zinc-800/30 border border-zinc-800/40 p-3 mb-6">
            <p className="text-[11px] text-zinc-400 text-center leading-relaxed">
              Terminal protegido. Acesso restrito a contas autorizadas.
            </p>
          </div>

          {passcodeError && (
            <div className="bg-red-500/10 border border-red-500/30 p-3 mb-4">
              <p className="text-[11px] text-red-400 text-center">{passcodeError}</p>
              {isInIframe && (
                <a
                  href={window.location.origin}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-2 flex items-center justify-center gap-1.5 text-[11px] text-blue-400 hover:text-blue-300 underline"
                >
                  Abrir terminal em nova aba para usar Google OAuth
                </a>
              )}
            </div>
          )}

          {/* Google Login Button */}
          <button
            onClick={handleGoogleLogin}
            data-testid="google-login-btn"
            className="w-full flex items-center justify-center gap-3 px-4 py-3 bg-zinc-800/50 border border-zinc-700 text-zinc-200 font-semibold text-sm hover:bg-zinc-800 hover:border-zinc-600 transition-all duration-150 mb-1"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
              <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 01-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
              <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 009 18z" fill="#34A853"/>
              <path d="M3.964 10.71A5.41 5.41 0 013.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 000 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" fill="#FBBC05"/>
              <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 00.957 4.958L3.964 6.29C4.672 4.163 6.656 2.58 9 3.58z" fill="#EA4335"/>
            </svg>
            Entrar com Google
          </button>
          {isInIframe && (
            <p className="text-[10px] text-zinc-600 text-center mb-3 leading-relaxed">
              Google OAuth requer nova aba — popups bloqueados no preview
            </p>
          )}
          {!isInIframe && <div className="mb-4" />}

          {/* Divider */}
          <div className="flex items-center gap-3 mb-4">
            <div className="flex-1 h-px bg-zinc-800" />
            <span className="text-[10px] text-zinc-600 font-mono">OU</span>
            <div className="flex-1 h-px bg-zinc-800" />
          </div>

          {/* Passcode Login */}
          <form onSubmit={handlePasscodeLogin} className="space-y-3">
            <div className="relative">
              <Key size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
              <input
                type="password"
                value={passcode}
                onChange={e => setPasscode(e.target.value)}
                placeholder="Código de acesso"
                className="w-full pl-9 pr-4 py-2.5 bg-zinc-900 border border-zinc-700 text-zinc-200 text-sm placeholder-zinc-600 focus:outline-none focus:border-zinc-500"
                autoComplete="off"
              />
            </div>
            <button
              type="submit"
              disabled={passcodeLoading || !passcode.trim()}
              className="w-full py-2.5 bg-blue-600/20 border border-blue-600/40 text-blue-300 text-sm font-semibold hover:bg-blue-600/30 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
            >
              {passcodeLoading ? "Entrando..." : "Entrar com Código"}
            </button>
          </form>

          <div className="mt-6 text-center">
            <p className="text-[9px] text-zinc-600 font-mono">
              ACESSO SOMENTE VIA WHITELIST
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

export function AuthCallback() {
  const hasProcessed = useRef(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const processSession = async () => {
      const hash = window.location.hash;
      const params = new URLSearchParams(hash.replace("#", ""));
      const sessionId = params.get("session_id");

      if (!sessionId) {
        setError("session_id nao encontrado");
        return;
      }

      try {
        const resp = await fetch(`${API}/auth/session/exchange?session_id=${encodeURIComponent(sessionId)}`, {
          credentials: "include",
        });

        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          setError(data.detail || "Falha na autenticacao");
          return;
        }

        const user = await resp.json();
        if (user.session_token) {
          localStorage.setItem("session_token", user.session_token);
          localStorage.setItem("user_info", JSON.stringify({
            user_id: user.user_id,
            email: user.email,
            name: user.name,
            picture: user.picture,
          }));
        }

        if (window.opener && !window.opener.closed) {
          window.close();
        } else {
          window.location.href = "/dashboard";
        }
      } catch (e) {
        setError("Erro de conexao com o servidor");
      }
    };

    processSession();
  }, []);

  if (error) {
    return (
      <div className="min-h-screen bg-[#09090B] flex items-center justify-center" data-testid="auth-error">
        <div className="border border-red-500/30 bg-red-500/10 p-6 max-w-sm text-center">
          <p className="text-sm text-red-400 mb-4">{error}</p>
          <button
            onClick={() => { window.location.href = "/login"; }}
            className="px-4 py-2 text-xs border border-zinc-700 text-zinc-300 hover:bg-zinc-800 transition-all"
            data-testid="auth-error-back-btn"
          >
            Voltar ao Login
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#09090B] flex items-center justify-center" data-testid="auth-callback">
      <div className="flex items-center gap-3">
        <div className="w-5 h-5 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
        <span className="text-sm text-zinc-400">Autenticando...</span>
      </div>
    </div>
  );
}
