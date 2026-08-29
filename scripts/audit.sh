#!/usr/bin/env bash
# Auditoria de dependências (R06/RR04): fail-fast, sem mascarar exit codes e com
# arquivo temporário exclusivo por execução (mktemp) — seguro para rodadas
# concorrentes de `make audit`.
set -euo pipefail

cd "$(dirname "$0")/.."

# Nota: o padrão do mktemp deve terminar em XXXXXX (BSD/macOS não substitui X's
# no meio do nome, o que recriaria um caminho global compartilhado — RR04).
REQS="$(mktemp "${TMPDIR:-/tmp}/rag-audit-reqs.XXXXXX")"
cleanup() { rm -f "$REQS"; }
trap cleanup EXIT

UV_BIN="${UV:-uv}"
NPM_BIN="${NPM:-npm}"

(cd backend && "$UV_BIN" export --frozen --all-groups --no-emit-project --no-hashes -o "$REQS")
(cd backend && "$UV_BIN" run pip-audit --strict -r "$REQS")
"$NPM_BIN" --prefix frontend audit
