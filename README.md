# RAG de Livros — Fase 1

Aplicação RAG privada e local para acervo de livros em português. Especificação em
`docs/rag/SPECIFICATION.md`; decisões em `docs/rag/NOTES.md`; tarefas em `docs/rag/TASKS.md`.

Tutorial prático para testar o estado atual com um livro:
`docs/rag/TUTORIAL_TESTE_COM_UM_LIVRO.md`.

## Pré-requisitos

- Python 3.12 (`requires-python = ">=3.12,<3.13"`)
- Node.js 22 LTS
- [uv](https://github.com/astral-sh/uv) 0.12.7 (gerenciador de dependências Python — decisão registrada em `docs/rag/NOTES.md`)
- Docker + Docker Compose (para PostgreSQL/pgvector e ambiente completo)

## Instalação

```bash
make setup        # uv sync --frozen (backend) + npm ci (frontend)
```

## Comandos de qualidade

```bash
make lint           # ruff + eslint
make format-check   # ruff format --check + prettier --check
make typecheck      # mypy + tsc --noEmit
make test           # pytest unit + vitest
make test-integration  # pytest integration (PostgreSQL via testcontainers)
make test-contract     # pytest contract (servidores HTTP simulados)
make test-e2e          # playwright (requer ambiente de pé)
make audit             # pip-audit + npm audit
make security-scan     # indicadores bloqueados na cadeia de dependências
```

## Estrutura

```text
backend/    Python 3.12 — domínio puro + aplicação + adapters + infraestrutura + api + cli
frontend/   React 19 + TypeScript (SPA) — consulta, sessões, leitor PDF
deploy/     Docker Compose e proxy (T20)
evaluation/ Benchmark e fixtures de avaliação (T19)
docs/rag/   Especificação, decisões, tarefas, checklist de revisão, matriz de evidências
```

## Segredos

Somente por variáveis de ambiente ou secret files. `.env.example` contém apenas
placeholders; nunca commitar `.env` real.
