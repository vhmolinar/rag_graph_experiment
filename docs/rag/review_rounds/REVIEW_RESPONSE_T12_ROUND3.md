# Resposta à revisão T12 — rodada 3

Data: 2026-09-02
Referência: `docs/rag/review_rounds/REVIEW_T12_ROUND3.md`
Status: aprovação com ressalva recebida; ressalva atendida.
Resultado anterior: **aprovado com ressalvas** (única ressalva: integração
PostgreSQL/migration não reproduzida no ambiente de revisão).

---

## Ressalva — integração PostgreSQL/migration não reproduzida no ambiente de revisão

### Diagnóstico
O revisor não dispõe de daemon Docker em `/var/run/docker.sock` e não pôde
repetir `uv run pytest tests/integration/test_context_pipeline.py -q` (os 10
erros ocorriam no setup do testcontainer, antes de qualquer teste do projeto).
A ressalva exige repetir esse comando num ambiente com Docker/Podman antes do
gate final da fase 1.

### Correção/evidência
Executado nesta sessão num ambiente com Podman (socket em
`/run/user/1000/podman/podman.sock`), contra PostgreSQL real via testcontainers
(pgvector/pgvector:0.8.6-pg17-bookworm), com as migrations aplicadas via Alembic
até `0007`:

| Comando | Resultado |
|---|---|
| `uv run pytest tests/integration/test_context_pipeline.py -q` | OK — 10 passed (PostgreSQL real) |
| `uv run pytest tests/integration -q` | OK — 211 passed, 1 skipped (PostgreSQL real) |

A execução confirma a migration `0007` (constraint `passages_check1` relaxado
para passagens multipágina) e o teste T12 multipágina com offsets invertidos
(`char_start=30`, `char_end=13`) reconstruindo o trecho exato e destacando
por página.

---

## Avaliação dos demais itens

- T12-R2-01, T12-01, T12-02 e T12-03 foram avaliados como **passa** na rodada 3
  (sem achados bloqueadores); nenhuna correção adicional foi aplicada.

---

## Evidências executadas (2026-09-02, Linux; PostgreSQL real via testcontainers sobre podman)

Ambiente: `DOCKER_HOST=unix:///run/user/1000/podman/podman.sock`,
`TESTCONTAINERS_RYUK_DISABLED=true`.

| Comando | Resultado |
|---|---|
| `uv run ruff check src tests` | OK — All checks passed |
| `uv run ruff format --check src tests` | OK — 109 arquivos |
| `uv run mypy src tests` | OK — 109 arquivos, strict, 0 issues |
| `uv run pytest tests/unit -q` | OK — 500 passed, 3 skipped (e2e opcionais) |
| `uv run pytest tests/integration/test_context_pipeline.py -q` | OK — 10 passed (PostgreSQL real) |
| `uv run pytest tests/integration -q` | OK — 211 passed, 1 skipped (PostgreSQL real) |

## Conclusão

Não restam achados bloqueadores no escopo de T12. A única ressalva da rodada 3
— a reprodução da integração PostgreSQL/migration num ambiente com
Docker/Podman — fica atendida pelas execuções acima contra PostgreSQL real
(211 passed, 1 skipped). O gate final da fase 1 pode repetir os comandos de
integração deste ambiente.
