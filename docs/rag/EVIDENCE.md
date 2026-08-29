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
| AC-03 | Passagem citada abre edição, página e trecho corretos | ◐ | T04: `test_artifacts.py` (armazenamento por hash, ranges); R01: integridade edição↔página/seção no banco; T05: `test_pages_and_offsets_recompose_excerpt` (offsets recompõem o trecho), `test_scan_ingest_preserves_original_identity` (identidade do original com derivado OCR); T06: `test_offsets_recompose_original_single_page`/`test_offsets_recompose_original_across_pages` (chunker puro), `test_indexes_pdf_with_page_offsets` (passagem persistida recompõe o trecho contra PostgreSQL real); falta o caminho passagem→leitor (T17) |
| AC-04 | Busca literal encontra frases exatas em português | ⬜ | T08/T19 |
| AC-05 | Busca semântica encontra paráfrases | ⬜ | T09/T19 |
| AC-06 | Rankings lexical, vetorial, RRF e reranking registrados | ◐ | T02: `test_candidates_record_all_stages`; T03: `test_full_roundtrip_with_all_stages_and_versions` (persistência JSONB dos 4 estágios) |
| AC-07 | Exclusão de obra vale em todos os estágios | ◐ | T02: `test_query.py` (filtros disjuntos) |
| AC-08 | Modo quote sem texto sintetizado | ◐ | T02: `test_answer.py::TestQuoteResponse` (garantia estrutural de tipo) |
| AC-09 | Dissertative sem afirmação factual sem evidência/inferência marcada | ◐ | T02: `test_answer.py::TestClaim` |
| AC-10 | Pergunta sem suporte produz abstenção | ◐ | T02: `test_answer.py::TestGeneratedAnswer` (contrato de abstenção) |
| AC-11 | Comparativa não usa uma obra só sem declarar limitação | ⬜ | T10/T12/T13 |
| AC-12 | Resumos levam a passagens; nunca citados | ◐ | T02: `test_knowledge.py::TestSummary`, `test_library.py::test_context_header_is_not_citable`; T06: `test_context_header_includes_work_and_section` (cabeçalho contextual sempre distinto do texto citável); resumos em si ficam para T11 |
| AC-13 | Contexto de sessão vira pergunta autônoma registrada | ⬜ | T10/T14/T15/T16 |
| AC-14 | Falha/timeout de modelo = erro tipado, sem fallback sem RAG | ◐ | T02: `errors.py` (hierarquia tipada); T07: `test_generation_adapter.py`/`test_reranker_adapter.py`/`test_embedding_adapter.py` (timeout, 429, 5xx, payload/dimensão inválidos sempre viram `ModelError` tipado); `test_embedding_adapter_resilience.py` (circuit breaker aberto falha fechado, sem tentar a rede). Falta: fluxo de geração completo não gerar prosa sem evidências (T13) |
| AC-15 | Resposta registra versões e evidências para reprodução | ◐ | T02: `test_versions.py`, `test_runs.py` (+`TestTransitions`, R05); T03: `test_version_tables_reject_update_and_delete`, `test_migration_is_deterministic_regardless_of_env` (R02), `test_prompt_version_identity_includes_template_hash` (R03), CHECKs terminais (R05); T06: `test_different_chunking_params_create_new_version` (reindexação nunca sobrescreve uma `ChunkingVersion`/`EmbeddingVersion` existente) |
| AC-16 | Logs/traces sem segredos nem texto integral | ◐ | T05: CLI com structlog (nomes de arquivo e ids apenas); `IngestReport`/`OcrReport` sem texto do livro; `test_error_does_not_leak_yaml_internals`; T07: `test_resilience.py::test_failure_logs_are_free_of_operation_content` (retry/circuit-breaker dos adapters de modelo só loga metadados — nunca prompts, documentos ou chaves, garantido por construção). Falta: API/traces (T18) |
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
  motor plugável (`auto|ocrmac|rapidocr|tesseract`); o derivado é o PRÓPRIO
  PDF original com uma camada de texto invisível sobreposta (imagem, página e
  dimensões preservadas), gravado atomicamente, acompanhado de um sidecar de
  proveniência verificável (`<output>.provenance.json`); nunca sobrescreve o
  original. Motores reais: e2e opcional (`RAG_OCR_E2E=1`, `rapidocr` local).
- **Serviço de ingestão** (`application/ingest.py`): validação de arquivo,
  metadados YAML (idioma: somente `pt` na fase 1), hash, deduplicação por
  `source_sha256`, extração e persistência em transação única (obra, edição,
  seções, páginas, artefato derivado OCR). Obra casada por título+autores
  (`WorksRepository.find_by_identity`). Contrato OCR §10.1.5: edição
  identifica a varredura original; derivado OCR versionado com
  `derived_from` = hash do original.
- **CLI** (`cli/main.py`, entry point `rag`): `ingest` (com `--dry-run`),
  `ocr` (com `--engine`), `inspect` (inclui warnings de extração); exit codes
  0/1/2; logs structlog JSON em stderr sem conteúdo do livro, caminhos
  absolutos ou traceback (`error_type` apenas; traceback completo só em
  `RAG_DEBUG_LOG`, quando configurado).

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

### T05 — Correções da revisão (`docs/rag/REVIEW_T05.md`, T5-01 a T5-10)

> **Seção histórica (correção R6-06):** descreve o estado logo após a
> primeira rodada de correções. Detalhes específicos abaixo foram
> substituídos por rodadas posteriores — ver "Segunda rodada" e "Terceira
> rodada" logo a seguir para o estado atual. Em particular:
> `_render_extrema` foi substituída por comparação pixel a pixel (R5-06,
> depois refinada); o sidecar `<output>.provenance.json` e o campo
> `output_sha256` descritos abaixo foram REMOVIDOS na terceira rodada
> (R6-01) — a proveniência agora vive embutida no próprio PDF; `pypdfium2`
> é dependência direta desde R5-09 (não mais só transitiva).

Todos os bloqueadores e correções importantes foram tratados antes de reenviar
T05. Resumo por item:

- **T5-01/T5-02 (bloqueador)** — `pdf_writer.py` foi reescrito: em vez de
  construir um PDF novo do zero, `write_text_layer_pdf` agora abre o PRÓPRIO
  PDF original via `pypdfium2` e insere objetos de texto invisíveis
  (`FPDF_TEXTRENDERMODE_INVISIBLE`) diretamente nas páginas existentes —
  preserva integralmente imagem, contagem, dimensões e rotação. Elimina
  também o problema de Unicode: `FPDFText_SetText` aceita UTF-16 nativamente,
  sem a codificação Latin-1 do gravador anterior. Evidência (nesta rodada):
  `test_text_layer_preserves_visual_content_and_is_searchable` (renderiza
  original e derivado e compara os pixels — extrema idêntico, não
  branco; comparação pixel a pixel só a partir de R5-06) e
  `test_unicode_text_round_trips` (travessão, aspas curvas, acentos
  portugueses, round-trip via extração real).
- **T5-03 (bloqueador)** — `_assert_coherent` agora compara toda a identidade
  imutável da ingestão (título, `source_type`, editora, ano, edição, ISBN,
  licença, e — via `Work` — título original, idioma e autores), não apenas
  título/edição/ISBN. Evidência: `test_divergent_metadata_field_conflicts`
  (parametrizado: authors, publisher, publication_year, license_status,
  original_title) e `test_source_type_divergence_rejected`.
- **T5-04 (bloqueador)** — `rag ocr` grava proveniência verificável
  (`OcrProvenance`: hash de entrada, engine/versão parcial, contagem de
  páginas) — nesta rodada, como sidecar `<output>.provenance.json`; a partir
  da terceira rodada (R6-01), embutida dentro do próprio PDF (ver seção
  correspondente mais abaixo). `rag ingest` valida a proveniência antes de
  aceitar o `ocr_artifact`: hash de entrada deve bater com o original sendo
  ingerido, e a contagem de páginas deve bater com o extraído. `generator`
  do `DerivedArtifactRef` passa a registrar o engine/versão parcial (ex.:
  `rapidocr:docling-2.123.1+rapidocr-backend=torch`, formato refinado em
  R6-05), não mais a string fixa `"docling-ocr"`.
  Evidência: `test_ocr_artifact_without_provenance_rejected`,
  `test_ocr_artifact_from_different_original_rejected`,
  `test_ocr_artifact_page_count_mismatch_rejected`.
- **T5-05 (correção importante)** — adicionado um e2e opcional (gate
  `RAG_OCR_E2E=1`, motor real `rapidocr`): varredura image-only → `rag ocr`
  → verificação de imagem preservada e alinhamento de páginas da
  proveniência. A fixture "scan" também deixou de ser uma página PDF vazia:
  `make_scanned_pdf` desenha um retângulo (conteúdo visual real, sem texto).
  Nesta rodada o teste ainda não provava reconhecimento real de texto nem
  executava `rag ingest` — corrigido na segunda rodada (ver abaixo:
  `make_scanned_pdf_with_text`, `test_real_engine_recognizes_text_and_preserves_image`,
  `TestOcrRealEngineToIngestE2E`).
- **T5-06 (correção importante)** — `docling_adapter.py`: item com múltiplas
  entradas de proveniência agora é dividido em um bloco por página usando o
  `charspan` de cada entrada; um `charspan` impreciso falha fechado em vez de
  atribuir o texto inteiro à primeira página. Evidência:
  `test_multi_page_provenance_splits_into_per_page_blocks`,
  `test_multi_page_provenance_without_charspan_fails_closed`.
- **T5-07 (correção importante)** — `_resolve_source` agora valida a
  combinação extensão×`source_type` contra uma matriz fechada
  (`.pdf` → `pdf_text`/`pdf_scan`; `.epub` → `epub`) nas DUAS direções, não
  só a antiga (EPUB declarado `pdf_text`/`pdf_scan` era aceito). Evidência:
  `TestExtensionSourceTypeMatrix` (as duas direções).
- **T5-08 (correção importante)** — novo campo persistido
  `Edition.extraction_warnings` (migration `0002`, coluna `jsonb`); `rag
  ingest` grava os warnings da extração na edição; `rag inspect` os exibe.
  Evidência: `test_extraction_warnings_roundtrip`,
  `test_extraction_warnings_default_empty` (round-trip contra PostgreSQL
  real).
- **T5-09 (correção importante)** — política de rótulos explicitada e
  fechada: `footnote`/`caption` viram parágrafos citáveis (não são mais
  descartados); qualquer rótulo de `TextItem` não mapeado (ex.: `code`)
  gera warning nomeado em vez de desaparecer silenciosamente. Evidência:
  `test_footnote_and_caption_are_preserved_as_text`,
  `test_unmapped_label_is_never_silently_dropped`.
- **T5-10 (correção importante)** — logging do CLI trocado de
  `KeyValueRenderer` para `JSONRenderer`; um processor dedicado
  (`_redact_exception`) remove o traceback do log de console (mantém só
  `error_type`) e só grava o traceback completo se `RAG_DEBUG_LOG` apontar
  para um arquivo. Evidência: `TestRedactException` (remove segredo/caminho
  do evento; grava traceback apenas quando configurado; é no-op sem
  `exc_info`).

Comandos reexecutados em 2026-08-29 após as correções:

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK |
| `make lint` / `make format-check` / `make typecheck` | OK |
| `make test` | OK — 223 unitários passed, 2 skipped (e2e docling e OCR opcionais), 1 frontend |
| `make test-integration` | OK — 84 passed (PostgreSQL real via testcontainers) |
| `make audit` | OK — nenhuma vulnerabilidade conhecida |
| `make security-scan` | OK — nenhum IOC bloqueado |

Correção de evidência (apontada pela revisão): "persistência em transação
única" vale apenas para o banco — os blobs do artifact store (original e
derivado OCR) são gravados antes da transação relacional e podem ficar
órfãos (mas nunca corrompidos) se a transação for abortada depois; órfãos
são detectáveis por `audit()` do artifact store (T04). O teste de scan usado
como evidência de AC-01/AC-02 agora exercita o próprio pipeline `rag ocr`
(`ocr_pdf`), não um PDF de OCR montado à mão.

### T05 — Segunda rodada de revisão (`docs/rag/REVIEW_T05_ROUND2.md`, R5-01 a R5-11)

> **Nota (correção R6-06):** R5-04 (`publish_derivative_pair`, sidecar) e
> R5-08 (`_engine_version`, campo `output_sha256`) descritos abaixo foram
> SUBSTITUÍDOS na terceira rodada — ver seção seguinte. `publish_derivative_pair`
> não existe mais; a proveniência vive embutida no PDF, sem `output_sha256`;
> o campo passou a se chamar `adapter_version` (não `engine_version`).

Resposta item a item na seção "Resposta à segunda revisão (ROUND2)" de
`docs/rag/REVIEW_RESPONSE_T05.md`. Correções R5-01–R5-11 aplicadas:

- **R5-01** (bloqueador) — `RapidOcrOptions(backend="torch")` explícito: o
  padrão da lib (`onnxruntime`) nunca foi dependência do projeto. E2e real
  reexecutado e verificado passando (`RAG_OCR_E2E=1`);
- **R5-02** (bloqueador) — verificação de hashes do sidecar OCR movida para
  antes de QUALQUER branch de retorno (dedup ou `--dry-run`); contagem de
  páginas verificada logo após a extração, também antes do branch de dry-run;
- **R5-03** (bloqueador) — reingestão de uma edição `pdf_scan` exige que o
  `ocr_artifact` informado seja exatamente (mesmo sha256) um derivado já
  registrado — trocar por outro derivado (mesmo com proveniência íntegra)
  conflita;
- **R5-04** (bloqueador) — `publish_derivative_pair`: PDF e sidecar são
  construídos e gravados (com fsync) em temporários ANTES de qualquer
  `os.replace`; falha em qualquer etapa de preparação não toca nenhum
  caminho final — um par publicado anteriormente permanece íntegro;
- **R5-05** — nova fixture `make_scanned_pdf_with_text` (Pillow, sem
  dependência nova) rasteriza uma frase legível — o e2e agora prova
  reconhecimento real (`report.lines > 0`, palavras esperadas no texto) e há
  um e2e completo adicional que executa `rag ingest` de fato
  (`TestOcrRealEngineToIngestE2E`);
- **R5-06** — comparação de preservação visual trocada de extrema para
  diferença pixel a pixel (`PIL.ImageChops.difference(...).getbbox() is
  None`); novo teste com página rotacionada (`/Rotate 90`);
- **R5-07** — `OcrPage.physical_index` (validado contra a posição na lista,
  falha fechado se fora de ordem) e `OcrLine.width` (texto invisível ajustado
  à largura detectada via transform corretivo, âncora na borda esquerda);
  rotação já era preservada (mesma página reutilizada), faltava o teste;
- **R5-08** — `_engine_version` anexa o `backend` resolvido quando a opção do
  motor o expõe (hoje: RapidOCR) — `docling-2.123.1+rapidocr-backend=torch`
  em vez de string genérica. Limitação documentada e justificada: `auto` não
  é resolvido a um motor concreto (Docling não expõe isso publicamente) e
  versões de binário/modelo por motor não são capturadas (fora de escopo
  proporcional — os hashes de entrada/saída já garantem o objetivo central
  do contrato de proveniência);
- **R5-09** — `pypdfium2==5.13.0` declarado como dependência direta em
  `pyproject.toml` (aprovado explicitamente pelo usuário nesta sessão);
  lockfile resolvido sem mudança de versão;
- **R5-10** — `logger_factory=structlog.PrintLoggerFactory(file=sys.stderr)`
  explícito (confirmado por probe: stdout vazio, stderr com o JSON); debug
  log criado com `os.open(..., 0o600)`, conteúdo passa por `_redact_secrets`
  antes de gravar, e toda a gravação é melhor-esforço (`except OSError:
  pass`) — nunca mascara a exceção original;
- **R5-11** — `_spans_of` só recorta `item.orig` pelos índices do `charspan`
  quando `len(item.orig) == len(item.text)` (alinhamento garantido); caso
  contrário cai para o texto normalizado do próprio span, sem alegar
  equivalência falsa.

### T05 — Terceira rodada de revisão (`docs/rag/REVIEW_T05_ROUND3.md`, R6-01 a R6-06)

Resposta item a item na seção "Resposta à terceira revisão (ROUND3)" de
`docs/rag/REVIEW_RESPONSE_T05.md`. Correção R6-01–R6-06 aplicadas:

- **R6-01** (bloqueador) — dois `os.replace()` consecutivos nunca são
  atômicos em conjunto: uma falha entre os dois publicava PDF e sidecar de
  gerações diferentes (reproduzido pela revisão). Redesenho: a proveniência
  passou a ser embutida como ANEXO dentro do próprio PDF derivado
  (`pdf_writer.embed_attachment`/`read_attachment`, via `PdfDocument.new_attachment`)
  antes da ÚNICA gravação atômica (`publish_file`, temporário+fsync+rename).
  Um único arquivo, uma única troca atômica — não existe mais um segundo
  arquivo para dessincronizar. `OcrProvenance.output_sha256` foi removido
  (não há mais nada com que ele pudesse divergir); `_verify_ocr_hashes` e
  `_assert_coherent` passaram a comparar contra o sha256 computado na hora a
  partir do arquivo. Evidência:
  `test_rename_failure_does_not_destroy_previous_valid_file`,
  `test_publish_failure_leaves_previous_file_intact` (nível `ocr_pdf`).
- **R6-02** (correção importante) — RapidOCR (logger próprio, stdlib
  `logging`) e a saída de `warnings.warn` do Torch escreviam caminhos
  absolutos de arquivos de modelo diretamente em stderr, por fora do
  structlog do CLI. `_harden_third_party_logging()` (chamada em
  `DoclingOcrEngine.__init__`): filtro de `logging` que reescreve mensagens
  do logger `RapidOCR` e do `logging.lastResort` reduzindo qualquer caminho
  absoluto ao nome do arquivo; `warnings.showwarning` substituído por uma
  versão que aplica a mesma redação. Evidência: reprodução independente do
  relatório confirmada corrigida; novo teste de subprocesso
  `test_real_engine_does_not_leak_absolute_paths_to_console` (stdout e
  stderr capturados separadamente, `RAG_OCR_E2E=1`).
- **R6-03** (correção importante) — quando o fallback de R5-11 ocorre
  (`orig` desalinhado com `text`), `_to_canonical` agora conta essas
  ocorrências e emite um warning explícito no `CanonicalDocument`
  (`"N bloco(s) sem texto original preservável... (R6-03)"`) — a perda deixa
  de ser silenciosa mesmo com o campo `original_text` preenchido. Evidência:
  `test_multi_page_provenance_falls_back_when_orig_misaligned` (agora
  também verifica o warning),
  `test_multi_page_provenance_aligned_emits_no_fidelity_warning`.
- **R6-04** (correção importante) — `OcrPage.width`/`height` eram aceitos
  sem verificação contra a página real. `_check_geometry` (nova função em
  `pdf_writer.py`) compara com tolerância de 1pt e falha fechado em
  divergência; `FPDFPage_GenerateContent()` tem seu retorno verificado (era
  ignorado); `_fit_width` passou a falhar fechado (antes retornava em
  silêncio) se os limites do objeto de texto não puderem ser obtidos.
  Evidência: `test_geometry_mismatch_rejected`,
  `test_geometry_within_tolerance_accepted`. Alinhamento pixel-perfeito de
  seleção permanece para quando o leitor (T17) existir — a checagem aqui é
  a coerência geométrica básica do artefato, que é escopo de T05.
- **R6-05** (correção importante) — **aprovado explicitamente pelo usuário**:
  campo renomeado de `engine_version` para `adapter_version`, com docstring
  deixando claro o escopo parcial (versão do Docling + nome do motor +
  backend resolvido quando exposto; NÃO resolve `auto`, versão de binário
  do Tesseract, ou hash de modelo do RapidOCR/ocrmac). Registrado como
  limitação aceita, não fixada silenciosamente — ver NOTES.md §10.5.
- **R6-06** (correção importante) — `EVIDENCE.md`: seções históricas da
  primeira e segunda rodadas marcadas com nota explícita apontando o que foi
  substituído (sidecar/`output_sha256`/`_render_extrema`/`publish_derivative_pair`/
  `engine_version`), em vez de deixar afirmações contraditórias sem
  indicação de qual é a atual.

Gates reexecutados em 2026-08-29 após as correções R6-01–R6-06:

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK |
| `make lint` / `make format-check` / `make typecheck` | OK |
| `make test` | OK — 233 unitários passed, 3 skipped (e2e opcionais); 1 frontend |
| `make test-integration` | OK — 90 passed, 1 skipped (e2e opcional) |
| `make audit` | OK — nenhuma vulnerabilidade conhecida |
| `make security-scan` | OK — nenhum IOC bloqueado |
| `RAG_OCR_E2E=1` (unit: 2 e2e reais + subprocesso R6-02) | OK — 2 passed |
| `RAG_OCR_E2E=1` (integration: e2e completo até `rag ingest`) | OK — 1 passed |

### T06 — Chunking e indexação ✅

Dependências novas declaradas nesta tarefa (dentro do conjunto aprovado,
NOTES.md §10.1 item 1, ainda não consumidas antes): `httpx==0.28.1`
(runtime — cliente do adapter de embeddings) e `respx==0.23.1` (dev —
contract test HTTP). Nenhuma dependência fora do conjunto aprovado.

Interpretações registradas em NOTES.md §10.6 antes de implementar: `rag
index` reextrai o documento via Docling em vez de reconstruir a partir de
`Section`/`Page` (T05 não persiste detalhe de bloco); hierarquia pai/seção-
folha sem embedding nos pais; passagem pode abranger mais de uma página
física; contagem de tokens/sentenças são heurísticas (nenhum tokenizador
real está no conjunto aprovado); `--force` apaga passagens existentes antes
de reindexar; adapter HTTP de embeddings mínimo (sem retries/circuit
breaker — isso é T07) construído agora porque "geração em lote de
embeddings" é entregável explícito desta tarefa.

Entregáveis:

- **Chunker estrutural e configurável** (`domain/chunking.py`, função pura
  `chunk_document`): agrupa sentenças por `section_path` completo (nunca
  mistura seções, a fortiori nunca mistura capítulos); nunca corta uma
  sentença (toda fronteira de janela cai numa fronteira de sentença);
  hierarquia pai/filho — cada seção-folha vira uma ou mais janelas-pai
  (`parent_target_tokens`), cada pai sub-dividido em janelas-filho
  (`child_target_tokens`/`child_overlap_tokens` configurável); offsets
  recompõem o trecho original via fatia exata de `CanonicalPage.text`
  (uma página) ou concatenação de fatias (múltiplas páginas); EPUB sem
  offsets (sem páginas físicas). `ChunkingParams` vira `ChunkingVersion.params`.
- **Serviço de indexação** (`application/index.py`, `IndexingService`):
  reextrai o artefato correto (original ou derivado OCR) do `ArtifactStore`
  por hash; casa `section_path`/`page_index` dos blocos recuperados com
  `Section`/`Page` já persistidos; cria/reusa `ChunkingVersion` e
  `EmbeddingVersion` (`VersionsRepository.get_or_create`, idempotente); gera
  embeddings em lote SÓ para os filhos; valida contagem e dimensão dos
  embeddings retornados contra a versão registrada ANTES de qualquer
  INSERT; persiste pais e filhos numa única transação; `--force` remove
  passagens existentes da edição antes de reindexar (sem `--force`,
  reindexar é idempotente — no-op).
- **Adapter HTTP de embeddings** (`adapters/embedding_adapter.py`,
  `OpenAiCompatibleEmbeddingProvider`): `POST {base_url}/embeddings`
  compatível com OpenAI, autenticação Bearer via `EmbeddingEndpointSettings`
  (env `EMBEDDING_*`), mapeamento de erro para `ModelTimeoutError`/
  `ModelUnavailableError`/`RateLimitError`/`ModelResponseError`; valida que
  a resposta tem uma dimensão consistente entre todos os vetores. Sem
  retries/circuit breaker/limite de concorrência — isso é T07.
- **CLI** (`rag index <edition-id> [--force]`): chunking + embeddings em
  lote para uma edição já ingerida; `rag inspect` passou a mostrar a
  contagem de passagens (pais/filhos).

Testes/evidências:

- chunks não cruzam capítulos: `test_chunks_never_mix_chapters`;
- frases não são cortadas: `test_sentences_are_never_cut` (splits só em
  fronteira de sentença, verificado recompondo cada span do chunk);
- offsets recompõem o trecho original: `test_offsets_recompose_original_single_page`,
  `test_offsets_recompose_original_across_pages` (unit, pura) e
  `test_indexes_pdf_with_page_offsets` (integração, PostgreSQL real);
- sobreposição configurável: `test_overlap_repeats_trailing_sentences`;
- hierarquia pai/filho: `test_parent_child_hierarchy` (pai sem
  `parent_index`, mesmo `section_path`, intervalo do pai contém o do filho);
- mudança de parâmetros cria nova versão: `test_different_chunking_params_create_new_version`;
- dimensão inesperada do embedding falha antes de persistir:
  `test_embedding_dimension_mismatch_fails_before_persisting`,
  `test_embedding_count_mismatch_fails_before_persisting` (ambos confirmam
  ZERO passagens persistidas após a falha);
- idempotência/`--force`: `test_reindex_without_force_is_idempotent`,
  `test_force_reindexes_and_replaces_passages`;
- contrato HTTP do adapter de embeddings: 14 testes em
  `test_embedding_adapter.py` (respx — sucesso, timeout, erro de conexão,
  429 com/sem `Retry-After`, 5xx, 4xx, corpo malformado, contagem/dimensão
  inconsistente, header de autenticação).

Comandos executados em 2026-08-29:

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK — 165 pacotes |
| `make lint` / `make format-check` / `make typecheck` | OK |
| `make test` | OK — 263 unitários passed, 3 skipped (e2e opcionais); 1 frontend |
| `make test-integration` | OK — 103 passed, 1 skipped (e2e opcional) |
| `make audit` | OK — nenhuma vulnerabilidade conhecida |
| `make security-scan` | OK — nenhum IOC bloqueado |

Critérios: AC-03 (offsets recompõem o trecho — coberto acima), AC-12 (via
`context_header` separado do texto citável — `Passage.text` nunca inclui o
cabeçalho), AC-15 (versões imutáveis via `ChunkingVersion`/`EmbeddingVersion`,
reindexação nunca sobrescreve uma versão existente).

Limitações conhecidas: contagem de tokens e fronteiras de sentença são
heurísticas (calibração fica para o benchmark de T19, NOTES.md §4);
`--force` remove passagens antigas sem reter histórico (aceitável hoje —
`summaries`/`concepts` de T11 ainda não existem); nenhum `ExtractionVersion`
é registrado por `rag index` (não há coluna para associá-lo no schema
atual).

### T07 — Adapters HTTP de modelos ✅

Nenhuma dependência nova (backend/dev já aprovado, NOTES.md §10.1). Interpretações
declaradas em NOTES.md §10.8 antes de implementar: retries/circuit breaker/limite de
concorrência implementados sobre `asyncio`/`httpx` puros (sem lib nova); auth por
secret file via convenção explícita `*_API_KEY_FILE`; reranking usa contrato HTTP
próprio (não existe endpoint de reranking na API OpenAI); geração usa JSON mode sobre
`/chat/completions`, sem streaming (streaming é T14); adapter de embeddings de T06
enriquecido, não substituído (contrato/testes de T06 preservados).

Entregáveis:

- **Resiliência compartilhada** (`adapters/resilience.py`): `CircuitBreaker` (máquina
  de 3 estados — closed/open/half-open — abre após N falhas consecutivas, meio-abre
  após timeout de reset) e `call_with_resilience` (retry com backoff exponencial só
  para falhas transitórias, sob limite de concorrência via `asyncio.Semaphore`).
  Função só recebe um closure opaco e exceções — nunca o corpo de requisição/resposta,
  o que garante por construção que seus logs não podem vazar prompts/chaves (AC-16).
- **Configuração compartilhada** (`adapters/model_settings.py`): `ModelAuthSettings`
  (auth por `api_key` OU `api_key_file`, nunca ambos — `ValidationError` caso
  contrário) e `ResilienceSettings` (retries/backoff/concorrência/circuit breaker),
  reaproveitados pelos três adapters via herança múltipla de `BaseSettings`.
- **Adapter de embeddings enriquecido** (`adapters/embedding_adapter.py`): mesmo
  contrato HTTP de T06, agora com retry/circuit-breaker/concorrência via
  `call_with_resilience` e auth por secret file via `ModelAuthSettings`.
- **Adapter de geração** (`adapters/generation_adapter.py`,
  `OpenAiCompatibleGeneratorProvider`): `POST {base_url}/chat/completions` com
  `response_format={"type":"json_object"}`; monta mensagem `system` (política +
  contrato de saída) e mensagem `user` com seções delimitadas (pergunta+contexto,
  escopo, evidências numeradas por `passage_id`, instrução de profundidade); valida a
  resposta como `GeneratedAnswer` via Pydantic; timeout maior por requisição quando
  `depth=deep` (`deep_timeout_seconds`).
- **Adapter de reranking** (`adapters/reranker_adapter.py`, `HttpRerankerProvider`):
  contrato explícito `POST {base_url}/rerank` (`{model, query, documents}` →
  `{"results": [{"index", "relevance_score"}]}`), reordena a resposta por `index` para
  devolver pontuações na ordem de `documents` (contrato do Protocol).
- **Doubles locais** (`tests/fixtures/model_doubles.py`): `FakeEmbeddingProvider`
  (vetor determinístico por hash do texto), `FakeRerankerProvider` (pontuação por
  sobreposição de termos) e `FakeGeneratorProvider` (fábrica de resposta configurável,
  com `abstention_answer` auxiliar); todos aceitam uma fila de exceções para simular
  falha transitória seguida de sucesso; todos satisfazem os Protocols
  (`isinstance(..., EmbeddingProvider)` etc., verificado em teste).

Testes/evidências:

- contract tests com servidor HTTP simulado (respx): `test_embedding_adapter.py` (14,
  preexistentes de T06, inalterados), `test_generation_adapter.py` (10),
  `test_reranker_adapter.py` (11) — cobrindo sucesso, autenticação Bearer, timeout,
  erro de conexão, 429 com/sem `Retry-After`, 5xx, 4xx, payload malformado e violação
  de contrato (`ModelResponseError`/dimensão ou índice inválidos);
- retry/circuit breaker/concorrência: `test_resilience.py` (14, unitário sobre
  `CircuitBreaker`/`call_with_resilience` com relógio simulado) e
  `test_embedding_adapter_resilience.py` (6, mesmo comportamento sobre o adapter real
  via respx — retry-então-sucesso, esgotamento de retries, 4xx não retentado, circuito
  abre e passa a rejeitar sem bater na rede, limite de concorrência serializa
  chamadas);
- autenticação por secret file: `test_model_settings.py` (6 — leitura, strip, arquivo
  ausente/vazio falha fechado, mútua exclusividade com `api_key`) e um teste dedicado
  em `test_embedding_adapter_resilience.py` que confirma o header `Authorization`
  carrega a chave lida do arquivo;
- confirmação de que chaves e payloads não aparecem em logs (AC-16):
  `test_resilience.py::test_failure_logs_are_free_of_operation_content`, via
  `structlog.testing.capture_logs()` — prova que um segredo presente apenas no corpo
  da operação nunca aparece nos logs emitidos pelo retry/circuit-breaker;
- doubles satisfazem os Protocols e são determinísticos: `test_model_doubles.py` (11).

Comandos executados em 2026-08-29 (backend; frontend inalterado nesta tarefa):

| Comando | Resultado |
|---------|-----------|
| `uv run ruff check src tests` | OK |
| `uv run ruff format --check src tests` | OK — 81 arquivos |
| `uv run mypy src tests` | OK — 81 arquivos |
| `uv run pytest tests/unit -q` | OK — 321 passed, 3 skipped |
| `uv run pytest tests/integration -q` | OK — 103 passed, 1 skipped (PostgreSQL real via testcontainers) |
| `bash scripts/audit.sh` | OK — 0 vulnerabilidades (pip-audit --strict) |
| `python3 scripts/security_scan.py .` | OK — nenhum IOC bloqueado |

Critérios: AC-14 (timeout/5xx/429/payload inválido do endpoint de modelo sempre viram
erro tipado — `ModelTimeoutError`/`ModelUnavailableError`/`RateLimitError`/
`ModelResponseError` — nunca uma resposta gerada sem evidências; circuit breaker aberto
também falha fechado, sem tentar a rede); AC-16 (prova estrutural + testada de que o
caminho de retry/circuit-breaker não pode vazar segredos ou payload — cobre os
adapters; API/traces continuam em T14/T18).

Limitações conhecidas: o wire contract de reranking é uma escolha do implementador
(não há convenção "compatível com OpenAI" para reranking), documentada em NOTES.md
§10.8 item 6 — se o endpoint real usado em produção divergir desse contrato, o adapter
precisará de ajuste; geração não cobre streaming (T14); nenhum destes adapters está
ainda conectado a um pipeline de recuperação/geração real (T08–T13 os consomem via os
Protocols de `rag.domain.providers`, já satisfeitos hoje pelos adapters HTTP e pelos
doubles).

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
