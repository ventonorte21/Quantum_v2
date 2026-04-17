"""
github_backup_service.py
━━━━━━━━━━━━━━━━━━━━━━━━
Backup automático periódico para GitHub (ventonorte21/Quantum_v2).

Dois tipos de backup:
  1. CÓDIGO  — ZIP do source via GitHub Contents API → code-backups/code-YYYY-MM-DD.zip
  2. DADOS   — export das collections Atlas (JSON.gz) via GitHub Contents API
               → pasta data-backups/YYYY-MM-DD/ no repositório

Requisito: secret GITHUB_TOKEN com scope "repo" no Replit.
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("github_backup")

# ─── Configurações ────────────────────────────────────────────────────────────
GITHUB_REPO   = "ventonorte21/Quantum_v2"
GITHUB_API    = "https://api.github.com"
DATA_BRANCH   = "main"                     # dados vão para a mesma branch em subpasta
DATA_PREFIX   = "data-backups"             # pasta raiz dos backups de dados

# Intervalo padrão (horas) entre backups automáticos
DEFAULT_INTERVAL_HOURS = 12

# Collections a exportar (pares: nome_collection → nome_arquivo)
COLLECTIONS_TO_EXPORT = [
    ("scalp_config",       "scalp_config"),
    ("scalp_trades",       "scalp_trades"),
    ("scalp_trader_state", "scalp_trader_state"),
    ("scalp_snapshots",    "scalp_snapshots"),
    ("scalp_history",      "scalp_history"),
    ("scalp_fills",        "scalp_fills"),
    ("positions",          "positions"),
]

# Workspace root (dois níveis acima de backend/services/)
WORKSPACE_ROOT = Path(__file__).parent.parent.parent

# ─── Estado global ────────────────────────────────────────────────────────────
_backup_task:   Optional[asyncio.Task] = None
_last_run:      Optional[str] = None
_last_status:   Optional[str] = None
_last_error:    Optional[str] = None
_is_running:    bool = False


# ══════════════════════════════════════════════════════════════════════════════
# GitHub API helpers
# ══════════════════════════════════════════════════════════════════════════════

def _get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN não configurado. "
            "Adicione o secret no painel de Secrets do Replit."
        )
    return token


def _gh_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"token {token}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _gh_get_file_sha(
    client: httpx.AsyncClient, token: str, path: str
) -> Optional[str]:
    """Retorna o SHA do ficheiro se existir (necessário para update via Contents API)."""
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    r = await client.get(url, headers=_gh_headers(token))
    if r.status_code == 200:
        return r.json().get("sha")
    return None


async def _gh_put_file(
    client: httpx.AsyncClient,
    token: str,
    path: str,
    content_bytes: bytes,
    message: str,
) -> bool:
    """Cria ou actualiza um ficheiro no repositório via Contents API."""
    sha = await _gh_get_file_sha(client, token, path)
    url = f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}"
    body: Dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode(),
        "branch":  DATA_BRANCH,
    }
    if sha:
        body["sha"] = sha
    r = await client.put(url, headers=_gh_headers(token), json=body, timeout=60)
    if r.status_code in (200, 201):
        logger.info("GitHub push OK: %s", path)
        return True
    logger.error("GitHub push FAIL %s: %s %s", path, r.status_code, r.text[:200])
    return False


# ══════════════════════════════════════════════════════════════════════════════
# Atlas export
# ══════════════════════════════════════════════════════════════════════════════

async def _export_collection(
    database, collection_name: str, limit: int = 0
) -> List[Dict]:
    """Exporta uma collection MongoDB para lista de dicts (sem _id)."""
    col = getattr(database, collection_name, None)
    if col is None:
        return []
    cursor = col.find({}, {"_id": 0})
    if limit:
        cursor = cursor.sort("_id", -1).limit(limit)
    docs = []
    async for doc in cursor:
        # Converte datetimes para ISO string
        for k, v in list(doc.items()):
            if hasattr(v, "isoformat"):
                doc[k] = v.isoformat()
        docs.append(doc)
    return docs


async def _export_all(database) -> Dict[str, Tuple[bytes, int]]:
    """
    Exporta todas as collections configuradas.
    Retorna dict: {nome → (conteúdo_gzip, n_docs)}
    """
    results: Dict[str, Tuple[bytes, int]] = {}
    for col_name, file_name in COLLECTIONS_TO_EXPORT:
        try:
            docs = await _export_collection(database, col_name)
            payload = json.dumps(docs, ensure_ascii=False, default=str).encode()
            compressed = gzip.compress(payload, compresslevel=6)
            results[file_name] = (compressed, len(docs))
            logger.info(
                "Export %s: %d docs → %.1f KB gzip",
                col_name, len(docs), len(compressed) / 1024,
            )
        except Exception as exc:
            logger.warning("Export %s falhou: %s", col_name, exc)
    return results



async def _push_code_via_api(
    client: httpx.AsyncClient, token: str, date_str: str
) -> Tuple[bool, str]:
    """
    Exporta o código fonte como ZIP e faz upload para o repositório via GitHub Contents API.
    Evita operações git directas (não permitidas no ambiente Replit).

    O arquivo code-YYYY-MM-DD.zip é guardado em code-backups/ na branch main.
    """
    import io, zipfile

    # Extensões e pastas a incluir no ZIP
    INCLUDE_DIRS  = ["backend", "frontend/src", "frontend/public"]
    INCLUDE_EXTS  = {".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md",
                     ".yml", ".yaml", ".sh", ".txt", ".css", ".html"}
    EXCLUDE_NAMES = {"__pycache__", "node_modules", ".git", ".pythonlibs",
                     "build", "dist", ".mypy_cache", ".pytest_cache"}

    buf = io.BytesIO()
    n_files = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for include_dir in INCLUDE_DIRS:
            src = WORKSPACE_ROOT / include_dir
            if not src.exists():
                continue
            for p in src.rglob("*"):
                # Ignorar pastas excluídas
                if any(ex in p.parts for ex in EXCLUDE_NAMES):
                    continue
                if p.is_file() and p.suffix in INCLUDE_EXTS:
                    arcname = str(p.relative_to(WORKSPACE_ROOT))
                    zf.write(p, arcname)
                    n_files += 1

        # Incluir ficheiros raiz relevantes
        for root_file in ["replit.md", "start_backend.sh", "start_production.sh",
                          "pyproject.toml", "requirements.txt"]:
            fp = WORKSPACE_ROOT / root_file
            if fp.exists():
                zf.write(fp, root_file)
                n_files += 1

    zip_bytes = buf.getvalue()
    logger.info(
        "Code ZIP: %d ficheiros → %.1f KB", n_files, len(zip_bytes) / 1024
    )

    path = f"code-backups/code-{date_str}.zip"
    msg  = f"backup(code): {date_str} ({n_files} ficheiros)"
    ok = await _gh_put_file(client, token, path, zip_bytes, msg)
    if ok:
        logger.info("GitHub push código OK: %s", path)
        return True, "OK"
    return False, f"upload falhou para {path}"


# ══════════════════════════════════════════════════════════════════════════════
# Backup principal
# ══════════════════════════════════════════════════════════════════════════════

async def run_backup(database, include_code: bool = True) -> Dict[str, Any]:
    """
    Executa o backup completo:
      1. Exporta collections Atlas → JSON.gz → GitHub Contents API
      2. (Opcional) git push do código → branch main

    Retorna sumário do backup.
    """
    global _last_run, _last_status, _last_error, _is_running

    if _is_running:
        return {"status": "already_running", "message": "Backup já em execução"}

    _is_running = True
    token = _get_token()
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    ts_str   = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    summary: Dict[str, Any] = {
        "started_at":   ts_str,
        "date":         date_str,
        "repo":         GITHUB_REPO,
        "data_pushed":  {},
        "code_pushed":  False,
        "errors":       [],
    }

    try:
        # ── 1. Dados Atlas ──────────────────────────────────────────────────
        logger.info("Backup iniciado: exportando Atlas → GitHub (%s)", date_str)
        exports = await _export_all(database)

        async with httpx.AsyncClient(timeout=120) as client:
            for file_name, (content_gz, n_docs) in exports.items():
                path = f"{DATA_PREFIX}/{date_str}/{file_name}.json.gz"
                msg  = f"backup(data): {file_name} {date_str} ({n_docs} docs)"
                ok   = await _gh_put_file(client, token, path, content_gz, msg)
                summary["data_pushed"][file_name] = {
                    "ok":    ok,
                    "docs":  n_docs,
                    "bytes": len(content_gz),
                    "path":  path,
                }
                if not ok:
                    summary["errors"].append(f"data push falhou: {file_name}")

            # ── Manifesto de metadados ──────────────────────────────────────
            manifest = {
                "backup_at":   ts_str,
                "repo":        GITHUB_REPO,
                "collections": {
                    name: {
                        "docs":  info["docs"],
                        "bytes": info["bytes"],
                        "ok":    info["ok"],
                    }
                    for name, info in summary["data_pushed"].items()
                },
            }
            manifest_bytes = json.dumps(manifest, indent=2).encode()
            await _gh_put_file(
                client, token,
                f"{DATA_PREFIX}/{date_str}/manifest.json",
                manifest_bytes,
                f"backup(manifest): {date_str}",
            )

        # ── 2. Código (ZIP via Contents API) ────────────────────────────────
        if include_code:
            async with httpx.AsyncClient(timeout=300) as code_client:
                code_ok, code_msg = await _push_code_via_api(code_client, token, date_str)
            summary["code_pushed"] = code_ok
            if not code_ok:
                summary["errors"].append(f"code zip push: {code_msg}")

        summary["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        summary["status"] = "ok" if not summary["errors"] else "partial"
        _last_status = summary["status"]
        _last_error  = "; ".join(summary["errors"]) if summary["errors"] else None

    except Exception as exc:
        logger.exception("Backup erro crítico: %s", exc)
        summary["status"] = "error"
        summary["errors"].append(str(exc))
        _last_status = "error"
        _last_error  = str(exc)
    finally:
        _is_running  = False
        _last_run    = ts_str

    logger.info(
        "Backup concluído: status=%s data=%s code=%s erros=%d",
        summary["status"],
        {k: v["docs"] for k, v in summary["data_pushed"].items()},
        summary.get("code_pushed"),
        len(summary["errors"]),
    )
    return summary


# ══════════════════════════════════════════════════════════════════════════════
# Scheduler periódico
# ══════════════════════════════════════════════════════════════════════════════

async def _backup_loop(database, interval_hours: float):
    """Loop assíncrono que executa o backup a cada interval_hours horas.
    Primeira execução imediata no startup; depois dorme interval_hours entre runs.
    """
    logger.info(
        "GitHubBackup scheduler iniciado (intervalo: %.1fh → %s)",
        interval_hours, GITHUB_REPO,
    )
    while True:
        try:
            logger.info("GitHubBackup: iniciando backup...")
            await run_backup(database, include_code=True)
        except Exception as exc:
            logger.error("GitHubBackup loop erro: %s", exc)
        await asyncio.sleep(interval_hours * 3600)


def start_backup_scheduler(database, interval_hours: float = DEFAULT_INTERVAL_HOURS):
    """
    Inicia o scheduler de backup em background.
    Deve ser chamado no startup do servidor.

    Só activo em produção (REPLIT_DEPLOYMENT=1) — evita backups duplicados
    quando o ambiente de desenvolvimento está a correr em paralelo.
    """
    global _backup_task

    # Só corre em produção — REPLIT_DEPLOYMENT=1 é definido automaticamente
    # pelo Replit nos ambientes deployed (não existe em dev).
    is_production = os.environ.get("REPLIT_DEPLOYMENT") == "1"
    if not is_production:
        logger.info(
            "GitHubBackup: ambiente de desenvolvimento detectado — "
            "scheduler desactivado (backups correm apenas em produção)."
        )
        return False

    # Verifica token antes de iniciar
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        logger.warning(
            "GitHubBackup: GITHUB_TOKEN não configurado — scheduler NÃO iniciado. "
            "Adicione o secret GITHUB_TOKEN para activar backups automáticos."
        )
        return False

    if _backup_task and not _backup_task.done():
        logger.info("GitHubBackup scheduler já em execução.")
        return True

    _backup_task = asyncio.create_task(
        _backup_loop(database, interval_hours),
        name="github_backup_loop",
    )
    logger.info(
        "GitHubBackup scheduler iniciado (intervalo=%.1fh, repo=%s)",
        interval_hours, GITHUB_REPO,
    )
    return True


def stop_backup_scheduler():
    """Cancela o scheduler de backup."""
    global _backup_task
    if _backup_task and not _backup_task.done():
        _backup_task.cancel()
        logger.info("GitHubBackup scheduler cancelado.")


def get_backup_status() -> Dict[str, Any]:
    """Retorna o estado actual do serviço de backup."""
    token_ok        = bool(os.environ.get("GITHUB_TOKEN", ""))
    is_production   = os.environ.get("REPLIT_DEPLOYMENT") == "1"
    scheduler_running = bool(_backup_task and not _backup_task.done())
    return {
        "scheduler_running": scheduler_running,
        "token_configured":  token_ok,
        "is_production":     is_production,
        "is_running":        _is_running,
        "last_run":          _last_run,
        "last_status":       _last_status,
        "last_error":        _last_error,
        "repo":              GITHUB_REPO,
        "interval_hours":    DEFAULT_INTERVAL_HOURS,
        "data_branch":       DATA_BRANCH,
        "data_prefix":       DATA_PREFIX,
    }
