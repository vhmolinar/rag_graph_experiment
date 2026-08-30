# Resposta à quinta revisão T06

Data: 2026-08-30  
Referência: `docs/rag/REVIEW_T06_ROUND5.md`  
Status: correções R5-T6-01 a R5-T6-05 implementadas e verificadas.

## R5-T6-01 — Backfill write-once e concorrente

`EditionsRepository.backfill_canonical_fingerprint()` agora usa compare-and-set
parametrizado:

- só grava quando `canonical_fingerprint IS NULL`;
- repetição com o mesmo valor é idempotente;
- valor existente divergente produz `ConflictError` e nunca é sobrescrito;
- duas execuções concorrentes convergem para a mesma identidade, sem
  last-writer-wins divergente.

Evidências de integração:

- `test_fingerprint_backfill_is_write_once_and_idempotent`;
- `test_fingerprint_backfill_never_overwrites_divergent_identity`;
- `test_concurrent_fingerprint_backfills_converge_idempotently`.

## R5-T6-02 — Fonte canônica de `pdf_scan`

Foi criada uma única função de resolução,
`resolve_edition_extraction_artifact()`, consumida tanto pelo indexador quanto
pelo backfill. Para `pdf_scan`, ela exige exatamente um derivado
`OCR_TEXT_LAYER` e retorna seu hash; o scan original permanece apenas como
identidade imutável da edição.

O backfill também compara páginas e seções reextraídas com o estado persistido
antes do compare-and-set.

Evidência:

- `test_fingerprint_backfill_uses_registered_ocr_derivative` executa ingestão
  e backfill reais de um scan e confirma que o fingerprint original é
  restaurado a partir do derivado OCR registrado.

## R5-T6-03 — Cobertura do serviço e CLI

Foram adicionados testes de serviço para sucesso, repetição, conflito,
concorrência e PDF scan, além do contrato CLI:

- `test_backfill_fingerprint_command_is_idempotent`;
- `TestBackfillFingerprintCommand::test_invalid_uuid_exit_one`.

Falhas permanecem fechadas e transacionais; o CLI retorna código diferente de
zero para UUID inválido ou `RagError`.

## R5-T6-04 — Docker e Podman portáveis

`make test-integration` voltou a ser neutro e respeita a resolução padrão do
Docker client/`DOCKER_HOST`. Para Podman existe um target explícito:

```text
make test-integration-podman
```

O socket padrão deriva de `XDG_RUNTIME_DIR`, sem UID fixo, e pode ser
sobrescrito por `PODMAN_SOCKET`.

## R5-T6-05 — Evidências AC-03 e AC-15

`docs/rag/EVIDENCE.md` foi atualizado com:

- regressão PDF de texto citável versus offsets;
- histórico append-only por `IndexRun`;
- fingerprint canônico e migration `0004`;
- backfill write-once, conflito, concorrência e seleção do derivado OCR;
- comandos separados para unit, contract e integração via Podman;
- resultados finais desta rodada.

## Verificações finais

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK — 165 pacotes |
| `make lint` | OK |
| `make format-check` | OK |
| `make typecheck` | OK — mypy em 73 arquivos e TypeScript |
| `make test-unit` | OK — 258 passed, 3 skipped |
| `make test-contract` | OK — 26 passed |
| `make test-integration-podman` | OK — 116 passed, 1 skipped |
| `make audit` | OK — pip-audit e npm audit sem vulnerabilidades conhecidas |
| `make security-scan` | OK — nenhum IOC bloqueado |
| `alembic heads` | OK — único head `0004` |

Nenhuma dependência foi adicionada e nenhum requisito ou critério de aceitação
foi alterado.
