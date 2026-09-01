# Resposta à segunda revisão T06

Data: 2026-08-30
Referência: `docs/rag/REVIEW_T06_ROUND2.md`
Resultado: correções R2-T6-01 a R2-T6-05 implementadas; integração PostgreSQL pendente de ambiente com daemon/container disponível.

## R2-T6-01 — `citable_text` e associação aos offsets

Arquivos: `backend/src/rag/domain/chunking.py` e `backend/src/rag/domain/library.py`.

O chunker agora distingue o caso em que `original_text` está alinhado ao texto
normalizado. Quando um bloco é dividido entre filhos, ou quando a forma
original não pode ser alinhada com segurança, o `original_text` do nó passa a
ser exatamente a concatenação do texto do chunk. Para PDF, essa política é
aplicada sempre que a forma original diverge do texto endereçado pelos
offsets. Assim, `Passage.citable_text` não apresenta mais o bloco inteiro para
um filho que contém apenas parte dele, e um destaque PDF sempre recompõe
exatamente a citação retornada.

Para blocos completos com alinhamento confiável, o original é preservado,
incluindo diferenças legítimas de representação em EPUB, que não possui
offsets físicos.

Evidências:

- `tests/unit/test_chunking.py::TestOriginalText`;
- `tests/unit/test_chunking.py::test_offsets_recompose_original_single_page`;
- `tests/unit/test_chunking.py::test_offsets_recompose_original_across_pages`;
- suíte focada: **50 passed**.

## R2-T6-02 — Conteúdo sem seção

Arquivo: `backend/src/rag/application/index.py`.

Passagens cujo `section_path` é vazio agora são persistidas com
`section_id=None`, conforme o modelo de domínio. O cabeçalho contextual ainda
contém obra e edição; o título de seção só é acrescentado quando existe.
Conteúdo anterior ao primeiro heading e documentos sem headings deixam de
causar falha artificial durante a publicação das passagens.

Não foi criada uma seção raiz sintética, preservando a distinção entre
conteúdo realmente não seccionado e uma seção editorial.

## R2-T6-03 — Identidade da representação canônica

Arquivos:

- `backend/src/rag/domain/canonical.py`;
- `backend/src/rag/domain/library.py`;
- `backend/src/rag/application/ingest.py`;
- `backend/src/rag/application/index.py`;
- `backend/src/rag/infrastructure/repositories/editions.py`;
- `backend/alembic/versions/0003_canonical_fingerprint.py`.

`CanonicalDocument.fingerprint()` produz SHA-256 determinístico sobre a
representação JSON canônica completa, incluindo ordem e quantidade de blocos,
ordinais, tipo, nível, texto normalizado, `original_text`, seção,
proveniência, offsets, páginas e warnings.

O fingerprint é calculado na ingestão e persistido em `editions`. Durante
`rag index`, a reextração é comparada ao fingerprint antes de gerar chunks ou
chamar o provedor de embeddings. Portanto, mudanças de fronteiras de blocos,
`section_path`, offsets ou `original_text`, mesmo mantendo páginas e headings,
falham fechadas.

A migration `0003_canonical_fingerprint` adiciona a coluna e sua validação de
formato hexadecimal. A `ExtractionVersion` continua sendo criada e associada
ao `IndexRun`, enquanto o fingerprint identifica o documento canônico usado
na ingestão.

## R2-T6-04 — Gate de contract tests

Arquivo: `Makefile`.

`make test-contract` agora coleta tanto `tests/contract` quanto
`tests/unit/test_embedding_adapter.py`, onde estão os testes HTTP com `respx`.

Comando executado:

```text
make test-contract
```

Resultado:

```text
26 passed
```

Não houve duplicação de testes: `tests/contract` permanece reservado para
testes classificados como contract e o arquivo unitário é incluído
explicitamente no gate por conter a cobertura HTTP existente.

## R2-T6-05 — Evidências desatualizadas

Arquivo: `docs/rag/EVIDENCE.md`.

Foi corrigida a referência ao teste de reindexação para
`test_force_reindexes_preserves_passage_history`, refletindo o comportamento
append-only por `IndexRun`.

## Verificações executadas

| Comando | Resultado |
|---|---|
| `make lint` | OK — Ruff e ESLint |
| `make typecheck` | OK — mypy e TypeScript |
| `make format-check` | OK |
| `make test-unit` | OK — 278 passed, 3 skipped |
| `make test-contract` | OK — 26 passed |
| `pytest` focado em chunking/domínio | OK — 50 passed |
| `make test-integration` | Não executado: daemon/socket Docker indisponível neste ambiente |

O erro inicial do `uv` por cache global somente leitura foi contornado usando
`UV_CACHE_DIR=/tmp/rag-uv-cache`; não representa falha do projeto.

## Dependências, desvios e riscos

Nenhuma dependência foi adicionada. Foi adicionada somente uma migration
Alembic, mantendo PostgreSQL como fonte de persistência.

Não há desvios arquiteturais aprovados ou não aprovados nesta correção.

Risco residual: os testes de integração que validam a aplicação da migration,
persistência do fingerprint e indexação PostgreSQL precisam ser reexecutados
em ambiente com PostgreSQL disponível. A ausência desse ambiente impede
declarar a validação de persistência completa como concluída.

## Complemento — rodada 3

Após a validação `REVIEW_T06_ROUND3.md`, foram corrigidos os pontos abaixo:

- blocos alinhados divididos entre filhos agora usam o texto exato de cada
  filho; regressão adicionada em `test_split_aligned_block_has_exact_text_per_child`;
- a migration foi renumerada para `0004`, com `down_revision="0003"`, após
  `0003_index_runs.py`; `alembic heads` retorna um único head (`0004`);
- edições sem fingerprint não são mais indexadas silenciosamente: o serviço
  falha fechado com `IngestionError` orientando backfill/reingestão
  administrativa. Não foi criado backfill automático, pois isso exigiria
  definir a versão histórica do extrator antes de reconstruir a representação;
- somente `section_path == ()` pode resultar em `section_id=None`; caminhos
  não vazios sem seção persistida produzem `IngestionError` sanitizado.

Verificações adicionais:

| Comando | Resultado |
|---|---|
| `uv run ruff check src tests` | OK |
| `uv run pytest tests/unit/test_chunking.py -q` | OK — 20 passed |
| `uv run alembic -c alembic.ini heads` | OK — único head `0004` |
