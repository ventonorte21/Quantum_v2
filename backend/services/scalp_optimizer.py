"""
Scalp Optimizer — Grid Search + Bayesian Optimization + Walk-Forward
====================================================================
Dois modos de optimização:
  GRID    — Gera grade de parâmetros e testa todas as combinações
  BAYESIAN — Gaussian Process + Expected Improvement (scipy/numpy)

Walk-Forward: divide scalp_snapshots em janelas roll-forward
  train_days → test_days → slide → repeat
  Produz métricas out-of-sample por janela + agregado.

Parâmetros optimizáveis (modo ZONES):
  score_strong_thresh   [3.0, 5.0]
  score_moderate_thresh [1.5, 3.5]
  ofi_slow_penalty      [0.5, 2.5]
  confluence_boost      [0.5, 2.5]

Parâmetros optimizáveis (modo FLOW):
  ofi_fast_strong       [0.20, 0.55]
  ofi_fast_moderate     [0.10, 0.30]
  s2_ofi_fast_min       [0.10, 0.35]
  s2_delta_ratio_min    [0.02, 0.15]
  s2_ofi_slow_min       [0.05, 0.25]
  s1_confidence_min     [3.0,  6.0]
  absorption_ofi_min    [0.40, 0.70]  ← novo

Parâmetros optimizáveis (modo CANDLE):
  body_ratio_strong     [0.45, 0.80]
  body_ratio_moderate   [0.20, 0.55]
  atr_sl_mult           [0.50, 1.50]
  atr_tp_mult           [1.00, 2.50]
  volume_min_moderate   [0.70, 1.10]
  s2_ofi_fast_min       [0.05, 0.20]  ← novo

Shared risk:
  sl_ticks              [4, 10]  (MNQ)
  tp_ticks              [8, 18]  (MNQ)
"""

import asyncio
import logging
import math
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
from scipy.stats import norm
from scipy.optimize import minimize

from services.scalp_replay_engine import run_scalp_replay, merge_scalp_config

logger = logging.getLogger("scalp_optimizer")


# ── Estado global ──────────────────────────────────────────────────────────────
_active_optimization: Optional[Dict] = None
_optimization_lock = asyncio.Lock()


# ════════════════════════════════════════════════════════════════════════════════
# Espaço de busca
# ════════════════════════════════════════════════════════════════════════════════

# ── Filtro de qualidade ───────────────────────────────────────────────────────
SEARCH_SPACE_QUALITY = {
    "min_quality_int": (0, 2),   # 0=WEAK  1=MODERATE  2=STRONG  (int)
}

# ── Parâmetros de sinal por modo ──────────────────────────────────────────────
SEARCH_SPACE_ZONES = {
    "zones.score_strong_thresh":      (3.0,  5.0),
    "zones.score_moderate_thresh":    (1.5,  3.5),
    "zones.ofi_slow_penalty":         (0.5,  2.5),
    "zones.confluence_boost":         (0.5,  2.5),
    # Thresholds de ativação da penalidade OFI slow — nunca calibrados para janela 150s
    # Fade zones: ofi_slow naturalmente contra → threshold mais alto (permissivo)
    # Momentum/pullback zones: slow deve confirmar → threshold mais baixo (exigente)
    "zones.ofi_slow_fade_thresh":     (0.40, 0.70),
    "zones.ofi_slow_momentum_thresh": (0.20, 0.45),
}

# ── Parâmetros ZONES por símbolo — otimizados independentemente ───────────────
# MNQ: micro Nasdaq — trends mais limpos, menor ruído relativo
SEARCH_SPACE_ZONES_MNQ = {
    "zones_mnq.score_strong_thresh":      (3.0,  5.0),
    "zones_mnq.score_moderate_thresh":    (1.5,  3.5),
    "zones_mnq.ofi_slow_penalty":         (0.5,  2.5),
    "zones_mnq.ofi_slow_fade_thresh":     (0.40, 0.70),
    "zones_mnq.ofi_slow_momentum_thresh": (0.20, 0.45),
}

# MES: micro S&P — maior ruído, thresholds mais exigentes necessários
SEARCH_SPACE_ZONES_MES = {
    "zones_mes.score_strong_thresh":      (3.0,  5.5),
    "zones_mes.score_moderate_thresh":    (2.0,  4.0),
    "zones_mes.ofi_slow_penalty":         (0.5,  3.0),
    "zones_mes.ofi_slow_fade_thresh":     (0.40, 0.75),
    "zones_mes.ofi_slow_momentum_thresh": (0.20, 0.50),
}

# ── Risco por símbolo — para otimizações focadas num único instrumento ────────
SEARCH_SPACE_RISK_MNQ = {
    "risk.sl_ticks_mnq": (4, 10),
    "risk.tp_ticks_mnq": (8, 18),
}

SEARCH_SPACE_RISK_MES = {
    "risk.sl_ticks_mes": (2, 8),
    "risk.tp_ticks_mes": (6, 14),
}

SEARCH_SPACE_FLOW = {
    "flow.ofi_fast_strong":    (0.20, 0.55),
    "flow.ofi_fast_moderate":  (0.10, 0.30),
    "flow.s2_ofi_fast_min":    (0.10, 0.35),
    "flow.s2_delta_ratio_min": (0.02, 0.15),
    "flow.s2_ofi_slow_min":    (0.05, 0.25),
    "flow.s1_confidence_min":  (3.0,  6.0),
    "flow.absorption_ofi_min": (0.40, 0.70),  # threshold de activação da absorção institucional
}

# ── Risco ─────────────────────────────────────────────────────────────────────
SEARCH_SPACE_RISK_CORE = {
    "risk.sl_ticks_mnq": (4,   10),
    "risk.tp_ticks_mnq": (8,   18),
    "risk.sl_ticks_mes": (2,   8),
    "risk.tp_ticks_mes": (6,   14),
}

SEARCH_SPACE_RISK_EXTRA = {
    "risk.slippage_ticks":         (0,   3),
    "risk.commission":             (0.0, 5.0),
    "risk.max_daily_loss_pct":     (2.0, 10.0),
    "risk.max_consecutive_losses": (2,   6),
    "risk.contracts":              (1,   3),
}

SEARCH_SPACE_RISK = {**SEARCH_SPACE_RISK_CORE, **SEARCH_SPACE_RISK_EXTRA}

# ── Espaços default por modo (lean — sem extras de risco por defeito) ─────────
MODE_SEARCH_SPACES = {
    # Modo base partilhado (sem símbolo específico)
    "ZONES":     {**SEARCH_SPACE_QUALITY, **SEARCH_SPACE_ZONES, **SEARCH_SPACE_RISK_CORE},
    # Modos per-símbolo: otimizam zones_mnq.* / zones_mes.* + risco do símbolo
    "ZONES_MNQ": {**SEARCH_SPACE_QUALITY, **SEARCH_SPACE_ZONES_MNQ, **SEARCH_SPACE_RISK_MNQ},
    "ZONES_MES": {**SEARCH_SPACE_QUALITY, **SEARCH_SPACE_ZONES_MES, **SEARCH_SPACE_RISK_MES},
    "FLOW":      {**SEARCH_SPACE_QUALITY, **SEARCH_SPACE_FLOW,  **SEARCH_SPACE_RISK_CORE},
    "ALL":       {**SEARCH_SPACE_QUALITY, **SEARCH_SPACE_ZONES, **SEARCH_SPACE_FLOW, **SEARCH_SPACE_RISK},
}

# ── Catálogo completo exposto ao frontend para configuração do espaço ─────────
PARAM_CATALOGUE = {
    **SEARCH_SPACE_QUALITY,
    **SEARCH_SPACE_ZONES,
    **SEARCH_SPACE_ZONES_MNQ,
    **SEARCH_SPACE_ZONES_MES,
    **SEARCH_SPACE_FLOW,
    **SEARCH_SPACE_RISK,
}

OBJECTIVE_KEYS = {
    "sharpe":         "sharpe_ratio",
    "sortino":        "sortino_ratio",
    "calmar":         "calmar_ratio",
    "profit_factor":  "profit_factor",
    "net_pnl":        "total_pnl",
    "expectancy":     "expectancy",
    "min_drawdown":   "max_drawdown",
}

# Metadata de grupo e rótulo por parâmetro — usado pelo endpoint /tune/params/space
GROUP_LABELS: Dict[str, tuple] = {
    "min_quality_int":              ("quality",    "Min Quality (0=WEAK 1=MOD 2=STR)"),
    "zones.score_strong_thresh":      ("zones",      "Score STRONG thresh"),
    "zones.score_moderate_thresh":    ("zones",      "Score MODERATE thresh"),
    "zones.ofi_slow_penalty":         ("zones",      "OFI-Slow penalty (magnitude)"),
    "zones.confluence_boost":         ("zones",      "Confluence boost"),
    "zones.ofi_slow_fade_thresh":     ("zones",      "OFI-Slow thresh (fade zones)"),
    "zones.ofi_slow_momentum_thresh": ("zones",      "OFI-Slow thresh (momentum zones)"),
    # Per-symbol ZONES params (MNQ)
    "zones_mnq.score_strong_thresh":      ("zones_mnq", "MNQ Score STRONG thresh"),
    "zones_mnq.score_moderate_thresh":    ("zones_mnq", "MNQ Score MODERATE thresh"),
    "zones_mnq.ofi_slow_penalty":         ("zones_mnq", "MNQ OFI-Slow penalty"),
    "zones_mnq.ofi_slow_fade_thresh":     ("zones_mnq", "MNQ OFI-Slow thresh (fade)"),
    "zones_mnq.ofi_slow_momentum_thresh": ("zones_mnq", "MNQ OFI-Slow thresh (momentum)"),
    # Per-symbol ZONES params (MES)
    "zones_mes.score_strong_thresh":      ("zones_mes", "MES Score STRONG thresh"),
    "zones_mes.score_moderate_thresh":    ("zones_mes", "MES Score MODERATE thresh"),
    "zones_mes.ofi_slow_penalty":         ("zones_mes", "MES OFI-Slow penalty"),
    "zones_mes.ofi_slow_fade_thresh":     ("zones_mes", "MES OFI-Slow thresh (fade)"),
    "zones_mes.ofi_slow_momentum_thresh": ("zones_mes", "MES OFI-Slow thresh (momentum)"),
    "flow.ofi_fast_strong":         ("flow",       "OFI-Fast STRONG"),
    "flow.ofi_fast_moderate":       ("flow",       "OFI-Fast MODERATE"),
    "flow.s2_ofi_fast_min":         ("flow",       "S2 OFI-Fast min"),
    "flow.s2_delta_ratio_min":      ("flow",       "S2 Delta-Ratio min"),
    "flow.s2_ofi_slow_min":         ("flow",       "S2 OFI-Slow min"),
    "flow.s1_confidence_min":       ("flow",       "S1 Confidence min"),
    "flow.absorption_ofi_min":      ("flow",       "Absorption OFI min"),
    "risk.sl_ticks_mnq":            ("risk_core",  "SL ticks MNQ"),
    "risk.tp_ticks_mnq":            ("risk_core",  "TP ticks MNQ"),
    "risk.sl_ticks_mes":            ("risk_core",  "SL ticks MES"),
    "risk.tp_ticks_mes":            ("risk_core",  "TP ticks MES"),
    "risk.slippage_ticks":          ("risk_extra", "Slippage ticks"),
    "risk.commission":              ("risk_extra", "Commission ($)"),
    "risk.max_daily_loss_pct":      ("risk_extra", "Max Daily Loss %"),
    "risk.max_consecutive_losses":  ("risk_extra", "Max Consecutive Losses"),
    "risk.contracts":               ("risk_extra", "Contracts"),
}


_MIN_TRADES_THRESHOLD = 3   # Mínimo de trades para o objectivo ser válido

def _extract_objective(metrics: Dict, objective: str) -> float:
    """Extrai valor do objectivo.

    Penaliza iterações com menos de _MIN_TRADES_THRESHOLD trades para
    impedir que o Gaussian Process convirja em regiões com 0 trades
    (onde sharpe=0 parece 'ótimo' comparado a resultados negativos).
    """
    if metrics.get("total_trades", 0) < _MIN_TRADES_THRESHOLD:
        return -5.0   # Penalidade fixa — menos que o pior sharpe real típico
    key = OBJECTIVE_KEYS.get(objective, "sharpe_ratio")
    val = metrics.get(key, 0.0)
    # Para min_drawdown: inverte o sinal (menor é melhor, mas maximizamos)
    return -val if objective == "min_drawdown" else val


# ════════════════════════════════════════════════════════════════════════════════
# Helpers: config flat ↔ aninhado
# ════════════════════════════════════════════════════════════════════════════════

def _flat_to_nested(flat: Dict) -> Dict:
    """Converte {'zones.score_strong_thresh': 4.0, ...} → config aninhado."""
    nested: Dict[str, Any] = {}
    for k, v in flat.items():
        if "." in k:
            section, param = k.split(".", 1)
            nested.setdefault(section, {})[param] = v
        else:
            nested[k] = v
    return nested


def _nested_to_flat(config: Dict, space: Dict) -> Dict:
    """Extrai apenas os parâmetros do espaço de busca do config."""
    flat = {}
    for k in space:
        if "." in k:
            section, param = k.split(".", 1)
            flat[k] = config.get(section, {}).get(param, space[k][0])
        else:
            flat[k] = config.get(k, space[k][0])
    return flat


# ════════════════════════════════════════════════════════════════════════════════
# Gaussian Process (numpy — sem sklearn)
# ════════════════════════════════════════════════════════════════════════════════

class ScalpGP:
    """
    Gaussian Process mínimo para Bayesian Optimization.
    Kernel: RBF + ruído (nugget).
    Treinado com scipy.optimize para hiperparâmetros de comprimento de escala.
    """

    def __init__(self, noise: float = 1e-6):
        self.noise   = noise
        self.X_train: Optional[np.ndarray] = None
        self.y_train: Optional[np.ndarray] = None
        self.K_inv:   Optional[np.ndarray] = None
        self._length_scale = 1.0
        self._signal_var   = 1.0

    def _rbf(self, X1: np.ndarray, X2: np.ndarray) -> np.ndarray:
        """RBF kernel: k(x1, x2) = σ² exp(−0.5 |x1−x2|² / l²)."""
        diff = X1[:, None, :] - X2[None, :, :]
        dist2 = np.sum(diff**2, axis=-1)
        return self._signal_var * np.exp(-0.5 * dist2 / self._length_scale**2)

    def fit(self, X: np.ndarray, y: np.ndarray):
        """Treina GP. X shape: (n, d), y shape: (n,)."""
        self.X_train = X.copy()
        self.y_train = (y - y.mean()) / (y.std() + 1e-8)
        self._y_mean = y.mean()
        self._y_std  = y.std() + 1e-8
        self._update_K_inv()

    def _update_K_inv(self):
        K = self._rbf(self.X_train, self.X_train)
        K += (self.noise + 1e-6) * np.eye(len(K))
        try:
            self.K_inv = np.linalg.inv(K)
        except np.linalg.LinAlgError:
            self.K_inv = np.linalg.pinv(K)

    def predict(self, X: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """Retorna (mu, sigma) para pontos X."""
        if self.X_train is None:
            return np.zeros(len(X)), np.ones(len(X))
        K_s  = self._rbf(X, self.X_train)
        K_ss = self._rbf(X, X)
        mu_norm  = K_s @ self.K_inv @ self.y_train
        var_norm = np.diag(K_ss - K_s @ self.K_inv @ K_s.T)
        var_norm = np.maximum(var_norm, 1e-8)
        mu  = mu_norm * self._y_std + self._y_mean
        sig = np.sqrt(var_norm) * self._y_std
        return mu, sig


def _expected_improvement(X: np.ndarray, gp: ScalpGP,
                           best_y: float, xi: float = 0.01) -> np.ndarray:
    """EI(x) = E[max(f(x) − best_y − ξ, 0)]."""
    mu, sigma = gp.predict(X)
    Z = (mu - best_y - xi) / (sigma + 1e-9)
    ei = (mu - best_y - xi) * norm.cdf(Z) + sigma * norm.pdf(Z)
    ei[sigma < 1e-10] = 0.0
    return ei


def _next_sample_bayesian(gp: ScalpGP, bounds: np.ndarray,
                           best_y: float, n_restarts: int = 5) -> np.ndarray:
    """Maximiza EI para escolher o próximo ponto a avaliar."""
    d = bounds.shape[0]
    best_x = None
    best_ei = -np.inf

    for _ in range(n_restarts):
        x0 = np.random.uniform(bounds[:, 0], bounds[:, 1])

        def neg_ei(x):
            return -_expected_improvement(x.reshape(1, -1), gp, best_y)[0]

        res = minimize(neg_ei, x0, bounds=[(b[0], b[1]) for b in bounds],
                       method="L-BFGS-B")
        if -res.fun > best_ei:
            best_ei = -res.fun
            best_x  = res.x

    return best_x if best_x is not None else np.random.uniform(bounds[:, 0], bounds[:, 1])


# ════════════════════════════════════════════════════════════════════════════════
# Grid Search
# ════════════════════════════════════════════════════════════════════════════════

def _linspace(lo: float, hi: float, steps: int) -> List[float]:
    if steps <= 1:
        return [lo]
    return [round(lo + i * (hi - lo) / (steps - 1), 4) for i in range(steps)]


def _resolve_space(mode: str, custom_space: Optional[Dict],
                   symbol: Optional[str] = None) -> Dict:
    """Retorna o espaço de busca efectivo: custom_space tem prioridade.

    Quando mode=ZONES e symbol é fornecido (MNQ/MES), selecciona automaticamente
    o espaço per-símbolo (ZONES_MNQ / ZONES_MES) para que o optimizer calibre
    os parâmetros zones_mnq.* / zones_mes.* em vez dos zones.* partilhados.
    """
    if custom_space:
        # custom_space chega como {param: [min, max]} ou {param: (min, max)}
        return {k: tuple(v) for k, v in custom_space.items()}
    mode_uc = mode.upper()
    # Auto per-símbolo: ZONES + símbolo específico → usa espaço especializado
    if mode_uc == "ZONES" and symbol:
        sym_mode = f"ZONES_{symbol.upper()}"
        if sym_mode in MODE_SEARCH_SPACES:
            return MODE_SEARCH_SPACES[sym_mode]
    return MODE_SEARCH_SPACES.get(mode_uc, MODE_SEARCH_SPACES["ZONES"])


def generate_scalp_grid(mode: str, grid_cfg: Dict,
                        custom_space: Optional[Dict] = None,
                        symbol: Optional[str] = None) -> List[Dict]:
    """
    Gera grade de parâmetros. grid_cfg suporta dois formatos:
      - Lista explícita: {"zones.score_strong_thresh": [4.5, 5.0, 5.5]}
      - Dict com steps:  {"zones.score_strong_thresh": {"steps": 4}}

    Comportamento de scope:
      - Se grid_cfg tem parâmetros explícitos → apenas esses formam a grade.
        Os restantes ficam nos valores do base_config (não incluídos no flat_params).
      - Se grid_cfg está vazio → usa todo o search space com steps=3 (modo legacy).
    """
    space = _resolve_space(mode, custom_space, symbol=symbol)

    # Scope: se o user especificou params em grid_cfg, usa só esses
    # (inclui também params que estão no custom_space e no grid_cfg)
    if grid_cfg:
        effective_params = {k: v for k, v in space.items() if k in grid_cfg}
        if not effective_params:
            # Fallback: grid_cfg tem keys que não estão no space — aceita tudo
            effective_params = space
    else:
        effective_params = space

    axes = {}
    for param, (lo, hi) in effective_params.items():
        spec = grid_cfg.get(param)
        is_int = isinstance(lo, int) and isinstance(hi, int)
        if isinstance(spec, (list, tuple)):
            # Valores explícitos fornecidos
            vals = [int(round(v)) for v in spec] if is_int else [float(v) for v in spec]
        else:
            # Dict {"steps": N} ou None → usa linspace
            steps = spec.get("steps", 3) if isinstance(spec, dict) else 3
            vals = _linspace(float(lo), float(hi), steps)
            if is_int:
                vals = [int(round(v)) for v in vals]
        axes[param] = vals

    # Produto cartesiano
    import itertools
    keys = list(axes.keys())
    combos = list(itertools.product(*[axes[k] for k in keys]))
    return [dict(zip(keys, combo)) for combo in combos]


# ════════════════════════════════════════════════════════════════════════════════
# Executores assíncronos
# ════════════════════════════════════════════════════════════════════════════════

async def _eval_flat_params(database, flat_params: Dict, base_cfg: Dict) -> Tuple[Dict, Dict]:
    """Avalia um conjunto de parâmetros flat. Devolve (metrics, full_result)."""
    nested = _flat_to_nested(flat_params)
    cfg = {**base_cfg}
    for section, params in nested.items():
        if isinstance(params, dict):
            cfg[section] = {**cfg.get(section, {}), **params}
        else:
            cfg[section] = params
    full_result = await run_scalp_replay(database, cfg)
    return full_result["metrics"], full_result


async def run_grid_search(database, opt_id: str, mode: str,
                          grid_cfg: Dict, base_config: Dict, objective: str,
                          custom_space: Optional[Dict] = None) -> None:
    """Executa grid search em background. Actualiza _active_optimization."""
    global _active_optimization

    _sym   = base_config.get("symbol")
    space  = _resolve_space(mode, custom_space, symbol=_sym)
    grid   = generate_scalp_grid(mode, grid_cfg, custom_space, symbol=_sym)
    total  = len(grid)
    results: List[Dict] = []
    best_obj  = -np.inf
    best_params: Optional[Dict] = None
    is_minimize = (objective == "min_drawdown")
    _t0 = time.perf_counter()

    logger.info("GRID [%s] início | %d combinações | objectivo=%s | opt_id=%s",
                mode, total, objective, opt_id)

    for i, flat_params in enumerate(grid):
        if _active_optimization and _active_optimization.get("cancelled"):
            _active_optimization["status"] = "cancelled"
            return

        if _active_optimization:
            _active_optimization["progress"] = {
                "current": i + 1, "total": total,
                "pct": round((i + 1) / total * 100, 1),
                "method": "GRID",
                "best_objective": round(best_obj if not is_minimize else -best_obj, 4),
                "best_params": best_params,
            }

        metrics, full_result = await _eval_flat_params(database, flat_params, base_config)
        obj_val = _extract_objective(metrics, objective)
        is_better = obj_val > best_obj and metrics["total_trades"] >= 3

        if is_better:
            best_obj    = obj_val
            best_params = flat_params
            logger.debug(
                "GRID [%s] iter %d/%d: novo best=%.4f trades=%d win=%.0f%% | params=%s",
                mode, i + 1, total, obj_val if not is_minimize else -obj_val,
                metrics["total_trades"], metrics.get("win_rate", 0) * 100, flat_params,
            )

        results.append(_make_result_entry(flat_params, obj_val, metrics, objective, is_minimize, full_result))

        if (i + 1) % 5 == 0:
            await asyncio.sleep(0)

    logger.info(
        "GRID [%s] concluído em %.1fs | %d avaliações | best=%.4f | params=%s",
        mode, time.perf_counter() - _t0, total,
        best_obj if not is_minimize else -best_obj, best_params,
    )
    _finalise_optimization(opt_id, results, objective, best_params,
                           best_obj, is_minimize, "GRID", base_config, mode)


async def run_bayesian_optimization(database, opt_id: str, mode: str,
                                    base_config: Dict, objective: str,
                                    n_random: int = 8, n_iter: int = 25,
                                    custom_space: Optional[Dict] = None) -> None:
    """
    Bayesian Optimization com Gaussian Process + Expected Improvement.

    Fluxo:
    1. n_random avaliações aleatórias (warm start)
    2. n_iter iterações guiadas por EI
    3. Persiste resultado e actualiza _active_optimization
    """
    global _active_optimization

    _sym     = base_config.get("symbol")
    space    = _resolve_space(mode, custom_space, symbol=_sym)
    params   = list(space.keys())
    bounds_list = [space[p] for p in params]
    bounds_np   = np.array([[float(b[0]), float(b[1])] for b in bounds_list])
    is_int   = [isinstance(b[0], int) for b in bounds_list]

    total = n_random + n_iter
    results: List[Dict] = []
    X_obs: List[np.ndarray] = []
    y_obs: List[float]      = []
    best_obj  = -np.inf
    best_params: Optional[Dict] = None
    gp = ScalpGP(noise=1e-4)
    is_minimize = (objective == "min_drawdown")
    _t0 = time.perf_counter()

    logger.info(
        "BAYESIAN [%s] início | random=%d iter=%d total=%d | objectivo=%s | opt_id=%s",
        mode, n_random, n_iter, total, objective, opt_id,
    )

    # Normaliza bounds para [0,1] internamente
    lo  = bounds_np[:, 0]
    hi  = bounds_np[:, 1]
    rng = hi - lo
    rng[rng == 0] = 1.0  # evita div/0

    def _scale(x_norm: np.ndarray) -> np.ndarray:
        """[0,1]ⁿ → espaço de parâmetros real."""
        x = lo + x_norm * rng
        for j, is_i in enumerate(is_int):
            if is_i:
                x[j] = round(x[j])
        return x

    def _norm(x_real: np.ndarray) -> np.ndarray:
        return (x_real - lo) / rng

    def _to_flat(x_real: np.ndarray) -> Dict:
        flat = {}
        for j, p in enumerate(params):
            v = x_real[j]
            flat[p] = int(round(v)) if is_int[j] else round(float(v), 4)
        return flat

    logger.info("BAYESIAN [%s] fase 1: %d pontos aleatórios", mode, n_random)

    # ── Fase 1: Random warm start ────────────────────────────────────────────
    for i in range(n_random):
        if _active_optimization and _active_optimization.get("cancelled"):
            _active_optimization["status"] = "cancelled"
            return

        x_norm = np.random.uniform(0, 1, len(params))
        x_real = _scale(x_norm)
        flat   = _to_flat(x_real)
        metrics, full_result = await _eval_flat_params(database, flat, base_config)
        obj_val = _extract_objective(metrics, objective)

        X_obs.append(x_norm)
        y_obs.append(obj_val)

        if obj_val > best_obj and metrics["total_trades"] >= 3:
            best_obj    = obj_val
            best_params = flat
            logger.debug(
                "BAYESIAN [%s] random %d/%d: novo best=%.4f trades=%d | params=%s",
                mode, i + 1, n_random,
                obj_val if not is_minimize else -obj_val,
                metrics["total_trades"], flat,
            )

        results.append(_make_result_entry(flat, obj_val, metrics, objective, is_minimize, full_result))

        if _active_optimization:
            _active_optimization["progress"] = {
                "current": i + 1, "total": total,
                "pct": round((i + 1) / total * 100, 1),
                "method": "BAYESIAN_RANDOM",
                "best_objective": round(best_obj if not is_minimize else -best_obj, 4),
                "best_params": best_params,
            }
        await asyncio.sleep(0)

    logger.info(
        "BAYESIAN [%s] fase 1 concluída: best_random=%.4f",
        mode, best_obj if not is_minimize else -best_obj,
    )
    logger.info("BAYESIAN [%s] fase 2: %d iterações GP/EI", mode, n_iter)

    # ── Fase 2: Iterações guiadas por GP/EI ─────────────────────────────────
    for i in range(n_iter):
        if _active_optimization and _active_optimization.get("cancelled"):
            _active_optimization["status"] = "cancelled"
            return

        # Treina GP
        X_arr = np.array(X_obs)
        y_arr = np.array(y_obs)
        gp.fit(X_arr, y_arr)

        # Escolhe próximo ponto via EI
        bounds_norm = np.ones((len(params), 2))
        bounds_norm[:, 0] = 0.0
        x_norm_next = _next_sample_bayesian(
            gp, bounds_norm, best_y=best_obj
        )
        x_real_next = _scale(x_norm_next)
        flat_next   = _to_flat(x_real_next)

        metrics, full_result = await _eval_flat_params(database, flat_next, base_config)
        obj_val = _extract_objective(metrics, objective)

        X_obs.append(_norm(x_real_next))
        y_obs.append(obj_val)

        if obj_val > best_obj and metrics["total_trades"] >= 3:
            best_obj    = obj_val
            best_params = flat_next
            logger.debug(
                "BAYESIAN [%s] GP %d/%d: novo best=%.4f trades=%d | params=%s",
                mode, i + 1, n_iter,
                obj_val if not is_minimize else -obj_val,
                metrics["total_trades"], flat_next,
            )

        results.append(_make_result_entry(flat_next, obj_val, metrics, objective, is_minimize, full_result))

        if _active_optimization:
            _active_optimization["progress"] = {
                "current": n_random + i + 1, "total": total,
                "pct": round((n_random + i + 1) / total * 100, 1),
                "method": "BAYESIAN_GP",
                "best_objective": round(best_obj if not is_minimize else -best_obj, 4),
                "best_params": best_params,
                "gp_n_obs": len(X_obs),
            }
        await asyncio.sleep(0)

    logger.info(
        "BAYESIAN [%s] concluído em %.1fs | %d avaliações | best=%.4f | params=%s",
        mode, time.perf_counter() - _t0, total,
        best_obj if not is_minimize else -best_obj, best_params,
    )
    _finalise_optimization(opt_id, results, objective, best_params,
                           best_obj, is_minimize, "BAYESIAN", base_config, mode)


def _make_result_entry(flat: Dict, obj_val: float,
                       metrics: Dict, objective: str, is_minimize: bool,
                       full_result: Optional[Dict] = None) -> Dict:
    entry = {
        "params":          flat,
        "objective_value": round(obj_val if not is_minimize else -obj_val, 4),
        "metrics": {k: metrics.get(k) for k in (
            "total_trades", "win_rate", "total_pnl",
            "sharpe_ratio", "sortino_ratio", "calmar_ratio", "profit_factor",
            "max_drawdown", "max_drawdown_pct", "expectancy",
        )},
    }
    if full_result:
        entry["by_zone_type"] = full_result.get("by_zone_type", {})
        entry["by_session"]   = full_result.get("by_session", {})
        entry["by_quality"]   = full_result.get("by_quality", {})
    return entry


def _finalise_optimization(opt_id: str, results: List[Dict], objective: str,
                            best_params: Optional[Dict], best_obj: float,
                            is_minimize: bool, method: str,
                            base_config: Dict, mode: str) -> None:
    """Ordena resultados e actualiza _active_optimization com resultado final."""
    global _active_optimization

    # Ordena: primeiro por ter trades suficientes (True > False), depois por objective.
    # Impede que iterações com 0 trades (obj≈0) apareçam acima de resultados reais negativos.
    results.sort(
        key=lambda r: (
            (r["metrics"].get("total_trades") or 0) >= _MIN_TRADES_THRESHOLD,
            r["objective_value"],
        ),
        reverse=True,
    )
    for rank, r in enumerate(results, 1):
        r["rank"] = rank

    finished_at = datetime.now(timezone.utc)
    started_at  = _active_optimization["started_at"] if _active_optimization else finished_at.isoformat()
    duration_s  = round((finished_at - datetime.fromisoformat(started_at)).total_seconds(), 1)

    final = {
        "optimization_id":  opt_id,
        "method":           method,
        "mode":             mode,
        "status":           "completed",
        "objective":        objective,
        "total_evaluations": len(results),
        "started_at":       started_at,
        "finished_at":      finished_at.isoformat(),
        "duration_s":       duration_s,
        "best": results[0] if results else None,
        "results": results[:80],
        "base_config": base_config,
    }

    _best_entry = results[0] if results else {}
    logger.info(
        "OPT [%s] finalizado | método=%s | avaliações=%d | duração=%.1fs | "
        "best_obj=%.4f | params=%s",
        mode, method, len(results), duration_s,
        _best_entry.get("objective_value", float("nan")),
        _best_entry.get("params"),
    )

    if _active_optimization:
        _active_optimization["status"] = "completed"
        _active_optimization["result"] = final
        _active_optimization["finished_at"] = finished_at.isoformat()


# ════════════════════════════════════════════════════════════════════════════════
# Walk-Forward Optimization
# ════════════════════════════════════════════════════════════════════════════════

async def run_walk_forward(database, base_config: Dict, objective: str = "sharpe",
                           method: str = "GRID", mode: str = "ZONES",
                           train_days: int = 10, test_days: int = 3,
                           n_folds: int = 4, n_random: int = 5,
                           n_iter: int = 15,
                           custom_space: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Walk-Forward Optimization em scalp_snapshots.

    Para cada fold:
      1. Treina (optimiza) na janela train
      2. Testa os melhores params na janela test (out-of-sample)
      3. Avança a janela

    Retorna métricas OOS agregadas e resultados por fold.
    """
    symbol = base_config.get("symbol", "MNQ")

    # Resolve datas de limite do utilizador (se fornecidas)
    def _wf_parse_date(val):
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        try:
            from datetime import timezone as _tz
            return datetime.fromisoformat(str(val).rstrip("Z")).replace(tzinfo=_tz.utc)
        except Exception:
            return None

    user_start = _wf_parse_date(base_config.get("start_date"))
    user_end   = _wf_parse_date(base_config.get("end_date"))
    if user_end:
        user_end = user_end.replace(hour=23, minute=59, second=59, microsecond=999999)

    # Encontra range de datas disponíveis (respeitando filtro do utilizador)
    date_filter: Dict[str, Any] = {"symbol": symbol}
    if user_start or user_end:
        date_filter["recorded_at"] = {}
        if user_start: date_filter["recorded_at"]["$gte"] = user_start
        if user_end:   date_filter["recorded_at"]["$lte"] = user_end

    newest_snap = await database.scalp_snapshots.find_one(
        date_filter, {"recorded_at": 1}, sort=[("recorded_at", -1)]
    )
    oldest_snap = await database.scalp_snapshots.find_one(
        date_filter, {"recorded_at": 1}, sort=[("recorded_at", 1)]
    )

    if not newest_snap or not oldest_snap:
        return {"error": "Sem snapshots disponíveis para walk-forward", "folds": []}

    t_end   = newest_snap["recorded_at"]
    t_start = oldest_snap["recorded_at"]

    # Ajusta: começa pelo início + train_days
    window_start = t_start + timedelta(days=train_days)

    folds: List[Dict] = []
    oos_trades_all: List[Dict] = []
    fold_n = 0
    _wf_t0 = time.perf_counter()

    logger.info(
        "WF [%s] início | %d folds | train=%dd test=%dd | método=%s | objectivo=%s | "
        "range=%s → %s",
        symbol, n_folds, train_days, test_days, method, objective,
        t_start.strftime("%Y-%m-%d"), t_end.strftime("%Y-%m-%d"),
    )

    current = window_start
    while fold_n < n_folds:
        train_start = current - timedelta(days=train_days)
        train_end   = current
        test_start  = current
        test_end    = current + timedelta(days=test_days)

        if test_end > t_end:
            break

        logger.info(
            "WF [%s] fold %d/%d | train=%s→%s | test=%s→%s",
            symbol, fold_n + 1, n_folds,
            train_start.strftime("%m-%d"), train_end.strftime("%m-%d"),
            test_start.strftime("%m-%d"), test_end.strftime("%m-%d"),
        )

        # ── Train: optimiza na janela de treino ───────────────────────────────
        train_cfg = {**base_config, "start_date": train_start, "end_date": train_end}

        if method == "BAYESIAN":
            best_flat = await _mini_bayesian(database, mode, train_cfg, objective,
                                             n_random, n_iter, custom_space)
        else:
            best_flat = await _mini_grid(database, mode, train_cfg, objective, custom_space)

        if not best_flat:
            current += timedelta(days=test_days)
            fold_n  += 1
            continue

        # ── Test: avalia melhores params fora-da-amostra ─────────────────────
        test_cfg = {**base_config, **_flat_to_nested(best_flat),
                    "start_date": test_start, "end_date": test_end}
        # merge aninhado
        merged = merge_scalp_config(test_cfg)
        for sec, pars in _flat_to_nested(best_flat).items():
            if isinstance(pars, dict):
                merged[sec] = {**merged.get(sec, {}), **pars}
            else:
                merged[sec] = pars
        oos_result = await run_scalp_replay(database, merged)

        fold_info = {
            "fold":        fold_n + 1,
            "train_start": train_start.isoformat(),
            "train_end":   train_end.isoformat(),
            "test_start":  test_start.isoformat(),
            "test_end":    test_end.isoformat(),
            "best_params": best_flat,
            "oos_metrics": oos_result["metrics"],
            "oos_trades":  oos_result["metrics"]["total_trades"],
        }
        folds.append(fold_info)
        oos_trades_all.extend(oos_result.get("trades", []))

        _oos_m = oos_result["metrics"]
        logger.debug(
            "WF [%s] fold %d OOS: trades=%d win=%.0f%% pnl=$%.2f | best_params=%s",
            symbol, fold_n + 1, _oos_m["total_trades"],
            _oos_m.get("win_rate", 0) * 100, _oos_m.get("total_pnl", 0),
            best_flat,
        )

        current += timedelta(days=test_days)
        fold_n  += 1

    # Agrega métricas OOS
    agg = _aggregate_oos_metrics(folds)

    logger.info(
        "WF [%s] concluído em %.1fs | %d/%d folds válidos | OOS trades=%d win=%.0f%% pnl=$%.2f",
        symbol, time.perf_counter() - _wf_t0, len(folds), n_folds,
        agg.get("total_trades", 0),
        agg.get("avg_win_rate", 0) * 100 if agg.get("avg_win_rate") else 0,
        agg.get("total_pnl", 0),
    )

    return {
        "method":      method,
        "mode":        mode,
        "symbol":      symbol,
        "objective":   objective,
        "train_days":  train_days,
        "test_days":   test_days,
        "n_folds":     len(folds),
        "folds":       folds,
        "aggregate":   agg,
    }


async def _mini_bayesian(database, mode: str, cfg: Dict, objective: str,
                         n_random: int, n_iter: int,
                         custom_space: Optional[Dict] = None) -> Optional[Dict]:
    """Versão reduzida do Bayesian para uso interno no walk-forward."""
    _sym   = cfg.get("symbol")
    space  = _resolve_space(mode, custom_space, symbol=_sym)
    params = list(space.keys())
    bounds_list = [space[p] for p in params]
    lo  = np.array([float(b[0]) for b in bounds_list])
    hi  = np.array([float(b[1]) for b in bounds_list])
    rng = hi - lo; rng[rng == 0] = 1.0
    is_int = [isinstance(b[0], int) for b in bounds_list]
    gp = ScalpGP()
    X_obs, y_obs = [], []
    best_flat, best_obj = None, -np.inf
    is_min = objective == "min_drawdown"

    def _to_flat(x: np.ndarray) -> Dict:
        return {p: (int(round(x[j])) if is_int[j] else round(float(x[j]), 4))
                for j, p in enumerate(params)}

    for _ in range(n_random):
        x_norm = np.random.uniform(0, 1, len(params))
        x_real = lo + x_norm * rng
        flat = _to_flat(x_real)
        m = await _eval_flat_params(database, flat, cfg)
        y = _extract_objective(m, objective)
        X_obs.append(x_norm); y_obs.append(y)
        if y > best_obj and m["total_trades"] >= 2:
            best_obj = y; best_flat = flat

    for _ in range(n_iter):
        gp.fit(np.array(X_obs), np.array(y_obs))
        bounds_norm = np.ones((len(params), 2)); bounds_norm[:, 0] = 0
        x_norm_next = _next_sample_bayesian(gp, bounds_norm, best_obj)
        x_real = lo + x_norm_next * rng
        flat = _to_flat(x_real)
        m = await _eval_flat_params(database, flat, cfg)
        y = _extract_objective(m, objective)
        X_obs.append((x_real - lo) / rng); y_obs.append(y)
        if y > best_obj and m["total_trades"] >= 2:
            best_obj = y; best_flat = flat

    return best_flat


async def _mini_grid(database, mode: str, cfg: Dict, objective: str,
                     custom_space: Optional[Dict] = None) -> Optional[Dict]:
    """Grid compacta (steps=3 por param) para uso interno no walk-forward."""
    _sym = cfg.get("symbol")
    grid = generate_scalp_grid(mode, {}, custom_space, symbol=_sym)  # steps=3 default
    best_flat, best_obj = None, -np.inf
    is_min = objective == "min_drawdown"
    for flat in grid[:50]:   # limita para não demorar demais
        m = await _eval_flat_params(database, flat, cfg)
        y = _extract_objective(m, objective)
        if y > best_obj and m["total_trades"] >= 2:
            best_obj = y; best_flat = flat
    return best_flat


def _aggregate_oos_metrics(folds: List[Dict]) -> Dict:
    """Agrega métricas OOS de todos os folds."""
    if not folds:
        return {}
    metrics_list = [f["oos_metrics"] for f in folds]

    def _avg(key):
        vals = [m.get(key, 0) for m in metrics_list if m.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    return {
        "avg_win_rate":       _avg("win_rate"),
        "avg_sharpe":         _avg("sharpe_ratio"),
        "avg_sortino":        _avg("sortino_ratio"),
        "avg_calmar":         _avg("calmar_ratio"),
        "avg_profit_factor":  _avg("profit_factor"),
        "total_oos_pnl":      round(sum(m.get("total_pnl", 0) for m in metrics_list), 2),
        "total_oos_trades":   sum(m.get("total_trades", 0) for m in metrics_list),
        "avg_max_drawdown":   _avg("max_drawdown"),
        "avg_expectancy":     _avg("expectancy"),
        "n_folds_with_trades": sum(1 for m in metrics_list if m.get("total_trades", 0) > 0),
    }


# ════════════════════════════════════════════════════════════════════════════════
# Interface pública
# ════════════════════════════════════════════════════════════════════════════════

async def start_scalp_optimization(
    database,
    method: str,          # "GRID" | "BAYESIAN"
    mode: str,            # "ZONES" | "FLOW" | "CANDLE"
    base_config: Dict,
    objective: str = "sharpe",
    grid_cfg: Dict = None,
    n_random: int = 8,
    n_iter: int = 25,
    min_snapshots: int = 20,
    custom_space: Optional[Dict] = None,
) -> Dict:
    """Inicia optimização em background. Retorna imediatamente com opt_id."""
    global _active_optimization

    symbol  = base_config.get("symbol", "MNQ")
    mode_uc = mode.upper()

    # ZONES_MNQ / ZONES_MES são variantes per-symbol de ZONES.
    # Os snapshots na DB estão gravados com mode="ZONES" (não "ZONES_MNQ").
    # Mapeamos para o modo base de snapshot antes de consultar a DB.
    _SNAP_MODE_MAP = {"ZONES_MNQ": "ZONES", "ZONES_MES": "ZONES"}
    snap_mode = _SNAP_MODE_MAP.get(mode_uc, mode_uc)

    # ── Conta snapshots por modo e determina mode_filter efectivo ─────────────
    # Se o modo específico tem dados suficientes → filtra por modo (mais puro).
    # Caso contrário → usa todos os modos disponíveis (fallback gracioso).
    n_snaps_mode = await database.scalp_snapshots.count_documents(
        {"symbol": symbol, "mode": snap_mode}
    )
    n_snaps_all = await database.scalp_snapshots.count_documents({"symbol": symbol})

    if n_snaps_all < min_snapshots:
        raise RuntimeError(
            f"Snapshots insuficientes: {n_snaps_all} disponíveis, mínimo {min_snapshots}. "
            "Aguarde mais dados de mercado (mercado aberto ≥ 10 min)."
        )

    if n_snaps_mode >= min_snapshots:
        effective_mode_filter = snap_mode   # e.g. "ZONES" (não "ZONES_MNQ")
        logger.info(
            "OPTIMIZER [%s] mode_filter=%s (%d snaps de %d totais) [snap_mode=%s]",
            mode_uc, effective_mode_filter, n_snaps_mode, n_snaps_all, snap_mode,
        )
    else:
        effective_mode_filter = None
        logger.warning(
            "OPTIMIZER [%s] snaps insuficientes para modo %s (%d < %d) — "
            "usando todos os modos (%d snaps). Resultados reflectem reevaluation "
            "por modo de cada snapshot.",
            mode_uc, snap_mode, n_snaps_mode, min_snapshots, n_snaps_all,
        )

    # Injeta mode_filter no base_config (sobrepõe valor enviado pelo frontend)
    base_config = {**base_config, "mode_filter": effective_mode_filter}

    async with _optimization_lock:
        if _active_optimization and _active_optimization.get("status") == "running":
            raise RuntimeError("Optimização já em execução — aguarde ou cancele a actual.")

        opt_id = str(uuid.uuid4())[:12]
        _active_optimization = {
            "optimization_id": opt_id,
            "method":          method,
            "mode":            mode,
            "status":          "running",
            "started_at":      datetime.now(timezone.utc).isoformat(),
            "finished_at":     None,
            "cancelled":       False,
            "objective":       objective,
            "progress":        {"current": 0, "total": n_random + n_iter if method == "BAYESIAN" else 0,
                                "pct": 0, "method": method},
            "result":          None,
        }

    if method == "BAYESIAN":
        asyncio.create_task(run_bayesian_optimization(
            database, opt_id, mode, base_config, objective, n_random, n_iter, custom_space
        ))
        total_evals = n_random + n_iter
    else:
        gc = grid_cfg or {}
        _sym_g = base_config.get("symbol")
        grid  = generate_scalp_grid(mode, gc, custom_space, symbol=_sym_g)
        total_evals = len(grid)
        _active_optimization["progress"]["total"] = total_evals
        asyncio.create_task(run_grid_search(database, opt_id, mode, gc, base_config, objective, custom_space))

    return {"optimization_id": opt_id, "status": "running",
            "method": method, "total_evaluations": total_evals}


def _sanitize_floats(obj):
    """Substitui nan/inf/-inf por None (JSON-safe) em dicts/listas aninhados."""
    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


def get_scalp_optimization_status() -> Optional[Dict]:
    if not _active_optimization:
        return None
    return _sanitize_floats({
        "optimization_id": _active_optimization["optimization_id"],
        "method":          _active_optimization.get("method"),
        "mode":            _active_optimization.get("mode"),
        "status":          _active_optimization["status"],
        "started_at":      _active_optimization["started_at"],
        "finished_at":     _active_optimization.get("finished_at"),
        "objective":       _active_optimization.get("objective"),
        "progress":        _active_optimization.get("progress", {}),
    })


def get_scalp_optimization_result() -> Optional[Dict]:
    if not _active_optimization or _active_optimization["status"] != "completed":
        return None
    return _sanitize_floats(_active_optimization.get("result") or {})


async def cancel_scalp_optimization() -> bool:
    global _active_optimization
    if _active_optimization and _active_optimization.get("status") == "running":
        _active_optimization["cancelled"] = True
        return True
    return False
