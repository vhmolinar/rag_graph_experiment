# Resposta à revisão T11 — rodada 3

Data: 2026-09-01
Referência: `docs/rag/review_rounds/REVIEW_T11_ROUND3.md`
Status: ressalva da rodada 3 resolvida — integrações reexecutadas num ambiente
com Podman configurado.
Resultado anterior: **aprovado com ressalvas** (R2-T11-01 corrigido; faltava
reexecutar as integrações PostgreSQL num ambiente com Docker/Podman).

---

## Ressalva de ROUND3 — integrações reexecutadas (2026-09-01)

A ressalva de `REVIEW_T11_ROUND3.md` ("A aprovação plena depende de reexecutar
essa suíte em ambiente com Docker ou Podman configurado") foi resolvida
reexecutando a suíte num ambiente com Podman configurado:

| Comando | Resultado |
|---|---|
| `DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration/test_enrichment_pipeline.py tests/integration/test_cli.py -q` | OK — 26 passed (PostgreSQL real via testcontainers) |
| `DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration -q` | OK — 201 passed, 1 skipped (e2e opcional) |

Comandos complementarios da rodada (já registrados em `docs/rag/EVIDENCE.md`):

| Comando | Resultado |
|---|---|
| `uv run ruff check src tests` | OK — All checks passed |
| `uv run ruff format --check src tests` | OK — 105 arquivos |
| `uv run mypy src tests` | OK — 105 arquivos, strict |
| `uv run pytest tests/unit -q` | OK — 469 passed, 3 skipped (e2e opcionais) |
| `make lock` / `python3 scripts/security_scan.py .` | OK — lockfiles consistentes; nenhum IOC bloqueado |

## Conclusão

A suíte de integração corre e passa num ambiente com Podman configurado
(201 passed, 1 skipped), cobrindo `test_enrichment_pipeline.py` e
`test_cli.py` (26 passed). A ressalva fica coberta: não restam achados
funcionais na correção de T11; AC-12 coberto para T11 e AC-15 parcial até
T13/T18, como concluíu o revisor.
