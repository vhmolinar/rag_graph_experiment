# Matriz de evidências AC-01 a AC-20 e registro de execução

Mantida pelo agente implementador. Cada critério só é marcado como coberto quando
existe teste automatizado + evidência reproduzível ligados a ele. O revisor independente
deve comparar esta matriz com o código e executar os comandos documentados.

Legenda: ⬜ pendente · ◐ parcial · ✅ coberto (com evidência)

## Matriz AC

| AC | Descrição resumida | Status | Evidências (testes / comandos) |
|----|--------------------|--------|--------------------------------|
| AC-01 | Reingestão idempotente, sem duplicar edição | ✅ | T03: `test_duplicate_source_hash_rejected`, `test_get_by_source_hash`; T04: dedup revalidado por hash (`TestConsistencyModel`); R08: replay divergente falha; T05: `test_reingest_is_idempotent`, `test_reingest_idempotent_exit_zero`, `test_divergent_metadata_same_file_conflicts` (mesma fonte + metadados divergentes falha fechado) |
| AC-02 | Duas edições da mesma obra distinguíveis e citáveis | ✅ | T02: `test_library.py::TestEdition`; T03: `test_two_editions_same_work_distinct`; R01: `TestCrossEditionIntegrity` (FKs compostas); T05: `test_two_editions_share_one_work` (mesmo Work, edições distintas, via ingestão real) |
| AC-03 | Passagem citada abre edição, página e trecho corretos | ◐ | T04: `test_artifacts.py` (armazenamento por hash, ranges); R01: integridade edição↔página/seção no banco; T05: `test_pages_and_offsets_recompose_excerpt` (offsets recompõem o trecho), `test_scan_ingest_preserves_original_identity` (identidade do original com derivado OCR); falta o caminho passagem→leitor (T06/T17) |
| AC-04 | Busca literal encontra frases exatas em português | ⬜ | T08/T19 |
| AC-05 | Busca semântica encontra paráfrases | ⬜ | T09/T19 |
| AC-06 | Rankings lexical, vetorial, RRF e reranking registrados | ◐ | T02: `test_candidates_record_all_stages`; T03: `test_full_roundtrip_with_all_stages_and_versions` (persistência JSONB dos 4 estágios) |
| AC-07 | Exclusão de obra vale em todos os estágios | ◐ | T02: `test_query.py` (filtros disjuntos) |
| AC-08 | Modo quote sem texto sintetizado | ◐ | T02: `test_answer.py::TestQuoteResponse` (garantia estrutural de tipo) |
| AC-09 | Dissertative sem afirmação factual sem evidência/inferência marcada | ◐ | T02: `test_answer.py::TestClaim` |
| AC-10 | Pergunta sem suporte produz abstenção | ◐ | T02: `test_answer.py::TestGeneratedAnswer` (contrato de abstenção) |
| AC-11 | Comparativa não usa uma obra só sem declarar limitação | ⬜ | T10/T12/T13 |
| AC-12 | Resumos levam a passagens; nunca citados | ◐ | T02: `test_knowledge.py::TestSummary`, `test_library.py::test_context_header_is_not_citable` |
| AC-13 | Contexto de sessão vira pergunta autônoma registrada | ⬜ | T10/T14/T15/T16 |
| AC-14 | Falha/timeout de modelo = erro tipado, sem fallback sem RAG | ◐ | T02: `errors.py` (hierarquia tipada) |
| AC-15 | Resposta registra versões e evidências para reprodução | ◐ | T02: `test_versions.py`, `test_runs.py` (+`TestTransitions`, R05); T03: `test_version_tables_reject_update_and_delete`, `test_migration_is_deterministic_regardless_of_env` (R02), `test_prompt_version_identity_includes_template_hash` (R03), CHECKs terminais (R05) |
| AC-16 | Logs/traces sem segredos nem texto integral | ◐ | T05: CLI com structlog (nomes de arquivo e ids apenas); `IngestReport`/`OcrReport` sem texto do livro; `test_error_does_not_leak_yaml_internals`. Falta: API/traces (T07/T18) |
| AC-17 | Conteúdo anonimizado expira em 90 dias | ⬜ | T18 |
| AC-18 | API com validação, CORS restrito, rate limiting, headers | ⬜ | T14/T16 |
| AC-19 | Benchmark repetível, compara sem sobrescrever | ⬜ | T19 |
| AC-20 | `docker compose up` funcional sem credenciais hardcoded | ⬜ | T20 |

## Registro por tarefa

### T01 — Estrutura do projeto e qualidade básica ✅

Decisões registradas em `docs/rag/NOTES.md` §10 (gerenciador: uv 0.12.7; conjunto de
dependências aprovado pelo usuário; introdução faseada por tarefa).

Comandos executados em 2026-08-28 (macOS arm64, Python 3.12.12, Node 22.21.1, uv 0.12.7):

| Comando | Resultado |
|---------|-----------|
| `cd backend && uv sync` | OK — 46 pacotes resolvidos, `uv.lock` gerado |
| `cd frontend && npm install` | OK — 0 vulnerabilidades, `package-lock.json` gerado |
| `make lock` | OK — `uv lock --check` + validação do package-lock |
| `make lint` | OK — ruff: all checks passed; eslint: 0 problemas |
| `make format-check` | OK — ruff format: 13 arquivos ok; prettier: ok |
| `make typecheck` | OK — mypy strict: 13 arquivos, 0 issues; tsc --noEmit: ok |
| `make test` | OK — pytest: 1 passed; vitest: 1 passed |
| `make audit` | OK — pip-audit --strict sobre lockfile exportado: nenhuma vulnerabilidade; npm audit: 0 |
| `make security-scan` | OK — sem plain-crypto-js, axios bloqueado ou sfrclak.com |

Limitações conhecidas: nenhuma nesta tarefa. Browsers do Playwright serão instalados em T16/T17.

### T02 — Modelo de domínio e contratos ✅

Entregáveis em `backend/src/rag/domain/`: `enums.py`, `errors.py`, `identifiers.py`,
`versions.py`, `library.py`, `knowledge.py`, `query.py`, `answer.py`, `runs.py`,
`providers.py`.

Comandos executados em 2026-08-28:

| Comando | Resultado |
|---------|-----------|
| `uv run pytest tests/unit -q` | 87 passed |
| `uv run ruff check src tests` | All checks passed |
| `uv run ruff format --check src tests` | 33 arquivos ok |
| `uv run mypy src tests` | Success: no issues found in 33 source files (strict) |

Destaques de invariantes (testes em `backend/tests/unit/`):

- pureza do domínio: `test_domain_purity.py` varre a AST e proíbe fastapi, starlette,
  sqlalchemy, alembic, psycopg, pgvector, docling, httpx, requests, openai, langchain;
- AC-02: edições da mesma obra distinguíveis (`test_library.py::TestEdition`);
- AC-08: `QuoteResponse` não possui campo de prosa (garantia estrutural);
- AC-09: `Claim` factual exige evidência; inferência é marcada;
- AC-10: abstenção exige razão e não carrega afirmações;
- AC-12: `Summary` exige ao menos uma passagem de suporte; `context_header` não é citável;
- AC-15: registros de versão frozen; `AnswerRun` com estados terminais coerentes;
- serialização: round-trips JSON e rejeição de valores inválidos (`test_serialization.py`).

### T03 — PostgreSQL, pgvector, migrações ✅

Entregáveis:

- `backend/alembic/versions/0001_initial_schema.py` — DDL completo: extensões
  (`vector`, `unaccent`, `pg_trgm`), configuração FTS `portuguese_unaccent`, 16 tabelas
  (obras, contribuidores, edições, artefatos derivados, seções, páginas, passagens,
  resumos, conceitos, aliases, evidências, execuções e 6 tabelas de versão), colunas
  geradas `text_search`, índices GIN (FTS), trigrama e HNSW (`vector_cosine_ops`),
  triggers PL/pgSQL de imutabilidade nas 6 tabelas de versão (AC-15);
- `backend/src/rag/infrastructure/config.py` — `DatabaseSettings` via pydantic-settings,
  senha como `SecretStr`;
- `backend/src/rag/infrastructure/db.py` — pool assíncrono psycopg3 com registro de
  vetores pgvector e contexto transacional (commit/rollback);
- `backend/src/rag/infrastructure/repositories/` — works, editions (com artefatos
  derivados, decisão OCR), content, passages (validação de dimensão do embedding contra
  `EmbeddingVersion` registrada — hook AC-15), versions (get_or_create idempotente),
  runs.

Comandos executados em 2026-08-28 (Docker via testcontainers, imagem
`pgvector/pgvector:0.8.6-pg17-bookworm`):

| Comando | Resultado |
|---------|-----------|
| `uv run pytest tests/integration -q` | 18 passed (PostgreSQL real em container) |
| `uv run pytest tests/unit -q` | 87 passed |
| `uv run ruff check src tests` | All checks passed |
| `uv run ruff format --check src tests` | 46 arquivos ok |
| `uv run mypy src tests` | Success: no issues found in 46 source files (strict) |

Evidências-chave:

- AC-01: `test_duplicate_source_hash_rejected` — UNIQUE violation em `source_sha256`
  mapeada para `ConflictError`;
- AC-02: `test_two_editions_same_work_distinct` + `test_derived_artifact_persisted`
  (decisão OCR: original imutável + derivado versionado com `derived_from`);
- AC-06: `test_run_roundtrip` — rankings dos 4 estágios persistidos em JSONB;
- AC-15: `test_version_tables_reject_update_and_delete` — trigger rejeita UPDATE/DELETE;
  `test_downgrade_and_reupgrade_on_throwaway_database` — migração reversível;
- índices: `test_index_usage.py` — `EXPLAIN (COSTS OFF)` com `enable_seqscan=off`
  confirma uso de GIN (FTS) e HNSW (vetorial) em dataset de 1500 passagens.

Limitações conhecidas: a coluna `passages.embedding` é `vector(1024)` fixa na
revisão 0001 (migration determinística — R02; sem variável de ambiente); mudança de
dimensão exige migração nova — estratégia documentada no cabeçalho da migração 0001.

### T04 — Armazenamento local de artefatos ✅

Entregáveis em `backend/src/rag/infrastructure/artifacts.py` e
`backend/src/rag/infrastructure/config.py` (`StorageSettings`):

- store endereçado por SHA-256 em volume configurável (`ARTIFACT_ROOT`), layout
  shardado `objects/ab/cd/<sha>` + sidecar `<sha>.meta.json`;
- gravação atômica: stream para `tmp/`, verificação de hash/tamanho/tipo, fsync,
  `os.replace`; falha em qualquer estágio remove o temporário;
- validação de tipo por magic bytes (PDF `%PDF-`; EPUB = ZIP com `mimetype`
  `application/epub+zip`), nunca por extensão; tamanho máximo configurável;
- proteção contra path traversal: chave é sempre o tipo `Sha256` (hex validado);
  `_object_path` confirma `is_relative_to(root)`; `original_filename` é apenas
  metadado sanitizado (`sanitize_filename` trata `\` e `/`);
- leitura com ranges (`read_range`, `open_stream`) com validação de limites;
- metadados por artefato: tipo, tamanho, data, nome original sanitizado;
- `cleanup_stale_temps` varre `.part` órfãos por idade.

Comandos executados em 2026-08-28:

| Comando | Resultado |
|---------|-----------|
| `uv run pytest tests/unit/test_artifacts.py -q` | 35 passed |
| `uv run ruff check src tests` | All checks passed |
| `uv run mypy src tests` | Success: no issues found in 48 source files |

Evidências-chave (TASKS.md T04): mesmo conteúdo não duplicado
(`test_same_content_not_duplicated` — flag `deduplicated` + mtime inalterado); hash
divergente falha e limpa temp (`test_divergent_hash_fails_and_cleans_temp`); nomes
maliciosos não escapam (`TestPathSafety` — tipo `Sha256` rejeita `../evil`, basename
sanitizado); ranges corretos (`TestReadRange` — início/meio/fim, clamp, erros de
limite); falha fechada com sidecar ausente/corrompido.

Limitações conhecidas: remoção física de artefatos antigos fica para operação
administrativa posterior (SPEC §Versionamento); I/O síncrono por ora (ingestão CLI);
endpoints HTTP usarão `asyncio.to_thread` se necessário (T14/T17).

### T05 — Representação canônica, adapters Docling e CLI de ingestão ✅

Desvio aprovado aplicado: `typer 0.26.8` (NOTES.md §10.1 item 4a) — `docling 2.123.1`
exige `typer <0.27.0`. Dependências declaradas nesta tarefa (dentro do conjunto
aprovado): docling 2.123.1, typer 0.26.8, pyyaml 6.0.3, structlog 26.1.0,
types-pyyaml (dev). Lockfile atualizado: 164 pacotes; `make audit` e
`make security-scan` limpos.

Entregáveis:

- **Schema canônico próprio** (`domain/canonical.py`): `CanonicalDocument` com
  blocos ordenados (heading/parágrafo), hierarquia de títulos, páginas físicas
  com rótulos, offsets por página, texto normalizado + original, warnings.
  Invariantes validadas: ordinais sequenciais, offsets aos pares e coerentes,
  `pdf_scan` nunca extraível diretamente, EPUB sem páginas, PDF exige páginas
  referenciadas. Golden file: `tests/fixtures/canonical_document.golden.json`.
- **Adapter Docling** (`adapters/docling_adapter.py`): PDF-texto e EPUB →
  canônico; mobília de página descartada; tabelas/figuras/fórmulas ignoradas
  com warning; PDF sem texto falha fechado orientando `rag ocr`. Núcleo de
  mapeamento testado com `DoclingDocument` programático (determinístico, sem
  modelo de layout nem rede); EPUB real offline; PDF real sem modelo via
  backend pypdfium injetado; pipeline padrão com modelo coberto por e2e
  opcional (`RAG_DOCLING_E2E=1`).
- **Adapter OCR separado** (`adapters/ocr_adapter.py` + `adapters/pdf_writer.py`):
  motor plugável (`auto|ocrmac|rapidocr|tesseract`); produz PDF derivado com
  camada de texto invisível posicionada, gravado atomicamente; nunca
  sobrescreve o original. Motores reais: e2e opcional (`RAG_OCR_E2E=1`).
- **Serviço de ingestão** (`application/ingest.py`): validação de arquivo,
  metadados YAML (idioma: somente `pt` na fase 1), hash, deduplicação por
  `source_sha256`, extração e persistência em transação única (obra, edição,
  seções, páginas, artefato derivado OCR). Obra casada por título+autores
  (`WorksRepository.find_by_identity`). Contrato OCR §10.1.5: edição
  identifica a varredura original; derivado OCR versionado com
  `derived_from` = hash do original.
- **CLI** (`cli/main.py`, entry point `rag`): `ingest` (com `--dry-run`),
  `ocr` (com `--engine`), `inspect`; exit codes 0/1/2; logs structlog em
  stderr sem conteúdo do livro nem caminhos absolutos.

Comandos executados em 2026-08-29 (mesmo ambiente de T01):

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK — 164 pacotes, lockfile consistente |
| `make lint` / `make format-check` / `make typecheck` | OK — ruff, prettier, mypy strict (64 arquivos), tsc |
| `make test` | OK — 213 unitários passed, 1 skipped (e2e docling opcional), 1 frontend |
| `make test-integration` | OK — 71 passed (PostgreSQL real via testcontainers) |
| `make audit` | OK — pip-audit strict: nenhuma vulnerabilidade; npm audit: 0 |
| `make security-scan` | OK — nenhum IOC bloqueado |

Evidências-chave por critério (ver matriz): AC-01 (`test_reingest_is_idempotent`,
`test_reingest_idempotent_exit_zero`, `test_divergent_metadata_same_file_conflicts`),
AC-02 (`test_two_editions_share_one_work`), AC-03
(`test_pages_and_offsets_recompose_excerpt` — offsets recompõem o trecho;
`test_scan_ingest_preserves_original_identity` — citação endereça o original),
parcial AC-16 (logs do CLI sem conteúdo/caminhos; `IngestReport`/`OcrReport`
não carregam texto).

Limitações conhecidas: pipeline padrão de PDF usa modelo de layout baixado do
HF Hub na primeira execução (cache local; pinagem de revisão do modelo é
interna ao docling 2.123.1); rótulos impressos de página usam o índice físico
+1 (Docling não extrai `PageLabels` na fase 1); OCR real depende de plataforma
(ocrmac no macOS) — contrato testado com stub, integração real opcional.

## Rodada de revisão T01–T04 (2026-08-29)

Revisão independente em `docs/rag/REVIEW_T01_T04.md`; resposta item a item em
`docs/rag/REVIEW_RESPONSE_T01_T04.md`. Todas as correções R01–R11 foram aplicadas
dentro do escopo T01–T04 (nenhuma tarefa T05+ implementada).

Comandos finais executados em 2026-08-29 após as correções:

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK |
| `make lint` | OK (ruff + eslint) |
| `make format-check` | OK |
| `make typecheck` | OK (mypy strict 49 arquivos; tsc) |
| `make test` | 150 passed (backend) + 1 passed (frontend) |
| `make test-integration` | 37 passed (PostgreSQL real via testcontainers) |
| `make audit` | pip-audit: nenhuma vulnerabilidade; npm audit: 0 |
| `make security-scan` | scanner estrutural: nenhum IOC bloqueado |

Nota de processo: a observação do revisor sobre `| tail` mascarar exit codes foi
incorporada — os comandos de verificação acima rodam via targets do Makefile (sem
pipes que escondam falhas) e `make audit` agora é um script fail-fast com teste de
fumaça que simula falhas (R06).

## Segunda rodada de revisão T01–T04 (2026-08-29)

Segunda revisão em `docs/rag/REVIEW_T01_T04_ROUND2.md`; resposta item a item na
seção "Resposta à segunda revisão (ROUND2)" de
`docs/rag/REVIEW_RESPONSE_T01_T04.md`. Correções RR01–RR05 aplicadas dentro do
escopo T01–T04:

- **RR01** — `summaries`: FK polimórfica eliminada; coluna tipada `section_id`
  com FK composta `(section_id, edition_id)` e CHECK de compatibilidade de
  escopo (`chapter` = Section de topo, documentado);
- **RR02** — `AnswerRun` profundamente imutável (tuplas + modelos aninhados
  frozen), `transition()` com allowlist de campos, append-only e versões
  registradas imutáveis; repository revalida o dump completo antes de persistir;
- **RR03** — sidecar validado contra chave (`sha256` tipado), tamanho e media
  type detectado; deduplicação executa verificação completa; `audit()` regenera
  sidecars divergentes;
- **RR04** — `scripts/audit.sh` usa `mktemp` exclusivo por execução; teste de
  concorrência com duas execuções paralelas;
- **RR05** — `EmbeddingVersion` com dimensão ≠ `vector(1024)` do schema é
  rejeitada no cadastro com `EmbeddingDimensionError`.

Comandos finais executados em 2026-08-29 após as correções:

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK |
| `make lint` | OK (ruff + eslint) |
| `make format-check` | OK |
| `make typecheck` | OK (mypy strict 50 arquivos; tsc) |
| `make test` | 161 passed (backend) + 1 passed (frontend) |
| `make test-integration` | 43 passed (PostgreSQL real via testcontainers) |
| `make audit` | pip-audit: nenhuma vulnerabilidade; npm audit: 0 |
| `make security-scan` | scanner estrutural: nenhum IOC bloqueado |
| `make audit` ∥ `make test` (concorrente) | ambos OK — corrida eliminada |

## Terceira rodada de revisão T01–T04 (2026-08-29)

Terceira revisão em `docs/rag/REVIEW_T01_T04_ROUND3.md`; resposta item a item na
seção "Resposta à terceira revisão (ROUND3)" de
`docs/rag/REVIEW_RESPONSE_T01_T04.md`. Correções RRR01–RRR03 aplicadas dentro do
escopo T01–T04:

- **RRR01** — imutabilidade profunda completa: `Claim.evidence_ids`,
  `EvidenceRef.section_path` e `Summary.supporting_passage_ids` em tuplas;
  `Summary` frozen; `AnswerRunsRepository.save()` limitado à allowlist de
  progresso, com comparação dos campos imutáveis contra o registro existente
  (`ConflictError` em divergência);
- **RRR02** — temporário de sidecar removido em `finally` e reconhecido por
  `audit()` como categoria própria (`sidecar_temps_removed`), nunca como objeto
  corrompido;
- **RRR03** — `embedding_versions.dimensions` com `CHECK (dimensions = 1024)`
  no banco; teste de paridade amarra migration, constante de infraestrutura e
  tipo físico da coluna.

Comandos finais executados em 2026-08-29 após as correções:

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK |
| `make lint` | OK (ruff + eslint) |
| `make format-check` | OK |
| `make typecheck` | OK (mypy strict 50 arquivos; tsc) |
| `make test` | 166 passed (backend) + 1 passed (frontend) |
| `make test-integration` | 48 passed (PostgreSQL real via testcontainers) |
| `make audit` | pip-audit: nenhuma vulnerabilidade; npm audit: 0 |
| `make security-scan` | scanner estrutural: nenhum IOC bloqueado |
## Quarta rodada de revisão T01–T04 (2026-08-29)

Quarta revisão em `docs/rag/REVIEW_T01_T04_ROUND4.md`; resposta item a item na
seção "Resposta à quarta revisão (ROUND4)" de
`docs/rag/REVIEW_RESPONSE_T01_T04.md`. Correções R4-01–R4-04 aplicadas dentro do
escopo T01–T04:

- **R4-01** — concorrência otimista em `answer_runs`: coluna `revision` com
  compare-and-swap no `save()` (`ConcurrencyError` tipado, distinto de
  `NotFoundError`) e trigger que impede regressão de estado terminal no banco;
- **R4-02** — proveniência de derivados imposta por FK composta
  `(edition_id, derived_from_sha256) → editions(id, source_sha256)`;
- **R4-03** — `chapter` é formalmente `Section` de topo (`level = 0`), imposto
  por trigger no banco;
- **R4-04** — descrição obsoleta de dimensão configurável removida deste
  documento (a coluna é `vector(1024)` fixa na revisão 0001).

Comandos finais executados em 2026-08-29 após as correções:

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK |
| `make lint` | OK (ruff + eslint) |
| `make format-check` | OK |
| `make typecheck` | OK (mypy strict 50 arquivos; tsc) |
| `make test` | 166 passed (backend) + 1 passed (frontend) |
| `make test-integration` | 58 passed (PostgreSQL real via testcontainers) |
| `make audit` | pip-audit: nenhuma vulnerabilidade; npm audit: 0 |
| `make security-scan` | scanner estrutural: nenhum IOC bloqueado |
| `make audit` ∥ `make test` (concorrente) | ambos OK |
