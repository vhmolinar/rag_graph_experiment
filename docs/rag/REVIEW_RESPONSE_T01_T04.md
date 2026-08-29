# Resposta à revisão T01–T04

Data: 2026-08-29
Referência: `docs/rag/REVIEW_T01_T04.md`
Resultado: **R01–R11 corrigidos**; nenhuma tarefa T05+ implementada. Sem discordâncias
técnicas — todos os achados foram aceitos e endereçados.

## R01 — Referências entre edições (bloqueador) ✅

Arquivos: `backend/alembic/versions/0001_initial_schema.py`.

- `sections`, `pages` e `passages` ganharam `UNIQUE (id, edition_id)`;
- FKs compostas: `passages.(section_id|page_start_id|page_end_id|parent_passage_id,
  edition_id)` → tabelas referenciadas `(id, edition_id)`; idem para
  `sections.(parent_section_id, edition_id)`;
- sínteses: `summaries.edition_id` (NOT NULL, FK para editions) + `UNIQUE (id,
  edition_id)` + CHECK `scope_type='edition' → scope_id = edition_id`;
  `summary_supports` ganhou `edition_id` com FKs compostas para `summaries` e
  `passages` — suporte nunca pertence a outra edição;
- conceitos permanecem globais por desígnio (SPEC §7.4: conceito normalizado agrega
  evidências de múltiplas edições); `concept_evidence` não exige mesma edição —
  documentado aqui como decisão consciente, não omissão;
- domínio: `Summary` ganhou `edition_id` + invariante de escopo (`knowledge.py`).

Testes: `TestCrossEditionIntegrity` (5 testes: página/seção/pai de outra edição
falham com `ForeignKeyViolation`; seção-pai cruzada falha; caso válido na mesma
edição funciona) e `test_edition_scope_requires_scope_id_equal_to_edition`.

## R02 — Migration determinística (bloqueador) ✅

Arquivos: `0001_initial_schema.py`, `tests/integration/test_migrations.py`.

- `RAG_EMBEDDING_DIMENSIONS` removida da revisão; dimensão fixa `1024` declarada no
  corpo da migration — a mesma revisão produz sempre o mesmo schema;
- validação antecipada: a revisão aborta se a dimensão exceder o limite do HNSW
  sobre `vector` (2000 no pgvector 0.8.x); dimensões maiores exigiriam `halfvec` em
  revisão própria;
- estratégia documentada no cabeçalho da revisão: avaliar Qwen com outra dimensão =
  nova migration (altera coluna + recria índice) + reindexação sob nova
  `embedding_version`; revisões antigas nunca mudam de significado.

Testes: `test_migration_is_deterministic_regardless_of_env` (upgrade com
`RAG_EMBEDDING_DIMENSIONS=4096` no ambiente produz `vector(1024)`);
`test_embedding_dimension_within_hnsw_limit` (constante da revisão ≤ 2000);
compatibilidade coluna↔versão registrada segue coberta por
`test_wrong_embedding_dimension_fails_before_persist`.

## R03 — Identidade de `PromptVersion` (bloqueador) ✅

Arquivos: `0001_initial_schema.py`, `repositories/versions.py`.

- constraint única agora é `(label, template_sha256, params)`;
- `VersionsRepository` usa o mesmo trio como conflict target/lookup;
- auditoria das demais tabelas de versão: `embedding_versions` já inclui
  `model_name` e `dimensions`; `model_endpoint_versions` inclui `endpoint_kind`,
  `provider`, `model_name`; `extraction/chunking/retrieval_policy` usam
  `(label, params)` onde `params` carrega, por convenção, todo parâmetro que altera
  comportamento — convenção agora documentada aqui e verificada por teste de
  distinção (`test_distinct_params_create_distinct_versions`);
- nenhum registro antigo é alterado: `get_or_create` só insere ou lê; triggers de
  imutabilidade continuam cobrindo UPDATE/DELETE.

Testes: `test_prompt_version_identity_includes_template_hash` (mesmo
label/params/hash → mesmo ID; hash diferente → ID diferente; ambos carregados com o
hash correto).

## R04 — Janela de inconsistência do ArtifactStore (bloqueador) ✅

Arquivos: `src/rag/infrastructure/artifacts.py`, `tests/unit/test_artifacts.py`.

Modelo adotado (documentado no docstring do módulo):

- o **objeto é autoritativo**; o sidecar é cache derivado e reparável;
- ordem de publicação invertida: objeto primeiro (tmp → fsync → `os.replace` →
  fsync do diretório), sidecar depois (tmp → fsync → `os.replace` → fsync);
- `metadata()` falha fechado (`NotFoundError`) quando o objeto não existe, mesmo
  com sidecar presente; valida `size_bytes` do sidecar contra o objeto;
- objeto sem sidecar (crash entre etapas) é **reparado** na leitura, após
  reverificação do hash — regra documentada;
- deduplicação revalida o objeto existente: recomputa hash e confere tamanho antes
  de aceitar; conteúdo divergente da chave falha fechado e **nunca é sobrescrito**;
- `verify_integrity(sha)` expõe a verificação completa (hash + sidecar);
- `audit()` varre o volume: remove sidecars órfãos, regenera sidecars ausentes
  (após verificação de hash) e reporta objetos corrompidos sem apagá-los (ação
  administrativa);
- limite de durabilidade documentado: crash de energia no instante do replace pode
  exigir `audit()` — verificável e recuperável.

Testes (`TestConsistencyModel` + ajustes): reparo de sidecar ausente na leitura;
sidecar órfão falha fechado e é removido por `audit()`; objeto corrompido rejeitado
na deduplicação e preservado; `audit()` repara/remove/reporta corretamente; dois
writers concorrentes para o mesmo hash preservam resultado íntegro; `.part` órfão é
invisível e varrido; divergência de tamanho sidecar↔objeto falha fechado.

## R05 — Bypass de invariantes por `model_copy` (bloqueador) ✅

Arquivos: `src/rag/domain/runs.py`, `src/rag/domain/errors.py`,
`repositories/runs.py`, `0001_initial_schema.py`, `tests/unit/test_runs.py`,
`tests/integration/test_repositories.py`.

- `AnswerRun` agora é `frozen=True` — atribuição direta é rejeitada;
- transições só via `AnswerRun.transition(status, **changes)`, que verifica o grafo
  permitido (`QUEUED→RUNNING→{SUCCEEDED|ABSTAINED|FAILED|CANCELLED}`, terminais sem
  saída) e **reconstrói o modelo via `model_validate`** — todas as invariantes
  terminais são reexecutadas; transição inválida levanta `InvalidTransitionError`
  (novo erro tipado, código `VALIDATION_ERROR`);
- `save()` usa `UPDATE ... RETURNING id` e levanta `NotFoundError` para ID
  inexistente;
- banco impõe a coerência terminal: CHECKs `failed → error_code`,
  `succeeded|abstained → response`, `abstained → response.abstained = true`;
- nota residual: `model_copy` é primitiva do Pydantic e não pode ser removida do
  tipo; a proteção efetiva é frozen + `transition()` como única porta + CHECKs no
  banco (defesa em profundidade). Nenhum uso de `model_copy(update=...)` permanece
  no código de produção ou nos testes.

Testes: `TestTransitions` (9 testes: frozen, caminho válido, regressão, salto de
estado, SUCCEEDED sem resposta, FAILED sem código, ABSTAINED sem resposta abstida,
terminais sem saída); integração: `test_save_with_unknown_id_fails`,
`test_db_rejects_succeeded_without_response`, `test_db_rejects_failed_without_error_code`,
`test_db_rejects_abstained_without_abstained_response`.

## R06 — `make audit` fail-fast ✅

Arquivos: `scripts/audit.sh` (novo), `Makefile`,
`backend/tests/unit/test_tooling.py`.

- recipe substituído por script com `set -euo pipefail` e cleanup via `trap EXIT` —
  falha de `uv export`, `pip-audit` ou `npm audit` aborta com status != 0;
- smoke tests (`TestAuditFailFast`): stubs de `uv`/`npm` simulam falha de export,
  falha de pip-audit, falha de npm e sucesso completo; verificam exit code e que
  etapas posteriores não executam; o temporário é sempre removido;
- descoberta incidental documentada no teste: `uv run` exporta `UV=<caminho real>`,
  então a injeção do stub usa a variável `UV`, não apenas `PATH`.

## R07 — Scan estrutural de IOCs ✅

Arquivos: `scripts/security_scan.py` (novo), `Makefile`, `test_tooling.py`.

- scanner Python (stdlib) analisa `package.json` (dependencies/devDependencies/
  optionalDependencies) e `package-lock.json` **estruturalmente**: entradas
  `node_modules/axios` em qualquer profundidade (transitivas) e o formato legado v1
  (`dependencies` aninhadas);
- reprova axios 1.14.1/0.30.4 em qualquer posição, `plain-crypto-js` em qualquer
  versão, ranges frouxos de axios (`^`, `~`, `*`, `latest`) e o domínio
  `sfrclak.com` em manifests/lockfiles Python e JS;
- independente de axios existir hoje: `test_safe_pinned_axios_passes` prova que a
  regra é de versão/pinagem, não de presença.

Testes (`TestSecurityScan`, 9 testes): fixtures de lockfile com dependência direta,
transitiva aninhada, formato v1, `plain-crypto-js`, range frouxo, domínio C2 em
`pyproject.toml`, projeto limpo e o repositório real.

## R08 — Conflito silencioso em seções/páginas ✅

Arquivos: `repositories/content.py`, `test_repositories.py`.

- `create_many` usa `ON CONFLICT ... DO NOTHING RETURNING id`; sem retorno, lê o
  registro existente e compara os campos relevantes (seção: parent, level, title,
  path, páginas; página: printed_label, text, text_sha256);
- repetição idêntica (mesmo com UUID novo) é idempotente; divergência levanta
  `ConflictError`.

Testes: `TestConflictDivergence` (4 testes: replay idêntico/divergente para seções
e páginas).

## R09 — Evidência de persistência de runs ✅

Arquivos: `test_repositories.py`.

- `test_full_roundtrip_with_all_stages_and_versions`: persiste e recarrega run com
  candidatos nos 4 estágios (LEXICAL, VECTOR, FUSED, RERANKED), evidências
  selecionadas, `VersionSet` com todos os campos preenchidos, latências, resposta
  com afirmação citada e `VerificationResult`; compara o objeto recarregado
  **integralmente** (`loaded == run`) e confirma os 4 estágios.

## R10 — Prova do índice trigram ✅

Arquivos: `tests/integration/test_index_usage.py`.

- `test_trgm_similarity_query_uses_gin_index`: `EXPLAIN (COSTS OFF)` da consulta
  com o operador `%` e `similarity()` sobre `rag_immutable_unaccent(text)` —
  exatamente a expressão indexada; o plano usa `passages_text_trgm_gin`
  (dataset de 1500 passagens, `enable_seqscan=off`).

## R11 — Lookup por hash da edição ✅

Arquivos: `test_repositories.py`.

- `test_get_by_source_hash`: create → lookup → mesmo ID; hash inexistente → `None`.

## Verificação final (2026-08-29)

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK |
| `make lint` | OK |
| `make format-check` | OK |
| `make typecheck` | OK (mypy strict, 49 arquivos; tsc) |
| `make test` | 150 passed backend + 1 passed frontend |
| `make test-integration` | 37 passed |
| `make audit` | nenhuma vulnerabilidade (pip-audit --strict; npm audit) |
| `make security-scan` | nenhum IOC bloqueado |

Observação de processo aceita: comandos de verificação não usam mais pipes que
mascarem exit codes; os resultados acima vêm dos targets do Makefile.

---

# Resposta à segunda revisão (ROUND2)

Data: 2026-08-29
Referência: `docs/rag/REVIEW_T01_T04_ROUND2.md`
Resultado: **RR01–RR05 corrigidos**; nenhuma tarefa T05+ implementada. Sem
discordâncias técnicas — todos os achados foram aceitos e endereçados.

## RR01 — Escopo de summaries referencialmente íntegro (bloqueador) ✅

Arquivos: `0001_initial_schema.py`, `src/rag/domain/knowledge.py`,
`tests/unit/test_knowledge.py`, `tests/integration/test_repositories.py`.

- FK polimórfica eliminada: `summaries.scope_id` substituído por coluna tipada
  `section_id uuid NULL` com FK composta `(section_id, edition_id) →
  sections(id, edition_id)` — a seção referenciada existe e pertence à edição
  da síntese;
- CHECK exige exatamente o campo compatível com o escopo:
  `(scope_type IN ('section','chapter') AND section_id IS NOT NULL) OR
  (scope_type='edition' AND section_id IS NULL)`;
- `chapter` é representado por `Section` (seção de topo) — documentado na
  migration e no docstring de `Summary`; escopo `edition` refere-se à própria
  `edition_id`, sem coluna extra;
- domínio: `Summary.section_id: UUID | None` com validator simétrico
  (`edition` rejeita section_id; `section`/`chapter` exigem).

Testes (4 exigidos, todos implementados):
1. `test_summary_section_scope_from_other_edition_fails` — FK violation;
2. `test_summary_section_scope_with_unknown_section_fails` — FK violation;
3. `test_summary_chapter_scope_without_section_fails` — CHECK violation;
4. `test_summary_valid_scopes_pass` — section/chapter/edition na mesma edição.
Domínio: `test_edition_scope_rejects_section_id`,
`test_section_and_chapter_scopes_require_section_id`.

## RR02 — Imutabilidade profunda e transições restritas do AnswerRun (bloqueador) ✅

Arquivos: `src/rag/domain/runs.py`, `answer.py`, `query.py`,
`infrastructure/repositories/runs.py`, `tests/unit/test_runs.py`,
`tests/integration/test_repositories.py`.

- Coleções do registro viraram tuplas: `AnswerRun.candidates`,
  `selected_evidence_ids`, `latencies`; `VersionSet.extraction_version_ids`,
  `prompt_version_ids`; `GeneratedAnswer.claims/limitations`;
  `QuoteResponse.evidences`; `VerificationResult.*`; `QueryPlan.subquestions/
  aliases/concept_labels`;
- modelos aninhados congelados: `GeneratedAnswer`, `QuoteResponse`,
  `VerificationResult` (Claim/RankedCandidate/StageLatency/EditionFilter já
  eram frozen);
- `transition()` com allowlist (`_ALLOWED_CHANGE_FIELDS`): id, created_at,
  pergunta (original/anonymized) e filtros explícitos são rejeitados;
  `candidates`/`latencies` são append-only; campos de `versions` já
  registrados não podem mudar (só preenchimento de campo ainda vazio);
  `RUNNING→RUNNING` adicionado ao grafo para progresso entre estágios;
- repository revalida o dump completo (`model_validate(model_dump(mode="json"))`)
  em `create()` e `save()` — instâncias de `model_copy(update=...)` inválidas
  são rejeitadas antes do SQL.

Testes: `TestDeepImmutability` (7 testes: append em tuplas, frozen aninhado,
campos proibidos, versão registrada imutável, append-only) e
`test_repository_revalidates_model_copy_bypass` (integração: `model_copy` para
`SUCCEEDED` sem resposta é rejeitado pelo `save()`).

## RR03 — Sidecar validado contra chave e conteúdo (bloqueador) ✅

Arquivos: `src/rag/infrastructure/artifacts.py`, `tests/unit/test_artifacts.py`.

- `ArtifactMetadata.sha256` tipado como `Sha256`;
- `metadata()` compara `sha256` do sidecar com a chave solicitada;
- `verify_integrity()` confere hash do conteúdo, chave, tamanho e media type
  detectado do objeto contra o sidecar;
- deduplicação (`_validated_existing`) agora executa `verify_integrity()`
  completa;
- níveis de confiança documentados no docstring de `metadata()` (leitura
  barata: existência + chave + tamanho; completa: `verify_integrity`);
- `audit()` regenera sidecar com sha/tamanho/tipo divergente após verificar o
  hash do objeto (política documentada no código).

Testes (3 exigidos, todos implementados):
1. `test_sidecar_with_wrong_sha_fails_closed`;
2. `test_sidecar_with_wrong_media_type_fails_verification_and_dedup`;
3. `test_audit_regenerates_inconsistent_sidecars`.

## RR04 — `make audit` seguro para concorrência ✅

Arquivos: `scripts/audit.sh`, `tests/unit/test_tooling.py`.

- arquivo de requirements exclusivo por execução via `mktemp` (padrão terminando
  em `XXXXXX` — BSD/macOS não substitui X's no meio do nome, o que recriaria um
  caminho global; comentário registrado no script);
- caminho absoluto passado ao `pip-audit`; `trap` remove somente o arquivo da
  própria execução;
- testes de tooling não tocam mais em arquivo global do repositório: o stub de
  `uv` honra o caminho `-o` recebido.

Teste: `test_concurrent_runs_do_not_interfere` — duas execuções paralelas com
janela alongada (`UV_EXPORT_SLEEP`) terminam com sucesso e sem arquivo global.
Verificação manual: `make audit & make test & wait` conclui sem erro.

## RR05 — EmbeddingVersion validada contra o schema no cadastro ✅

Arquivos: `src/rag/infrastructure/schema.py` (novo),
`infrastructure/repositories/versions.py`, `tests/integration/test_repositories.py`.

- constante única `EMBEDDING_COLUMN_DIMENSIONS = 1024` espelha a migration 0001
  (mudança futura exige migration + constante juntas — documentado);
- `VersionsRepository.get_or_create` rejeita `EmbeddingVersion` com dimensão ≠
  1024 com `EmbeddingDimensionError` tipado, antes de qualquer ingestão;
- teste existente que registrava dimensão 8 corrigido para 1024.

Teste: `test_embedding_version_rejects_dimension_outside_schema` (falha no
cadastro com erro tipado; dimensão 1024 aceita).

## Gates finais (2026-08-29, após RR01–RR05)

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK |
| `make lint` | OK (ruff + eslint) |
| `make format-check` | OK |
| `make typecheck` | OK (mypy strict 50 arquivos; tsc) |
| `make test` | 161 passed (backend) + 1 passed (frontend) |
| `make test-integration` | 43 passed (PostgreSQL real via testcontainers) |
| `make audit` | pip-audit: nenhuma vulnerabilidade; npm audit: 0 |
| `make security-scan` | nenhum IOC bloqueado |
| `make audit` ∥ `make test` (concorrente) | ambos OK — corrida eliminada |

---

# Resposta à terceira revisão (ROUND3)

Data: 2026-08-29
Referência: `docs/rag/REVIEW_T01_T04_ROUND3.md`
Resultado: **RRR01–RRR03 corrigidos**; nenhuma tarefa T05+ implementada. Sem
discordâncias técnicas — todos os achados foram aceitos e endereçados.

## RRR01 — Imutabilidade profunda e proteção do repository completas (bloqueador) ✅

Arquivos: `src/rag/domain/answer.py`, `knowledge.py`,
`infrastructure/repositories/runs.py`, `tests/unit/test_answer.py`,
`tests/unit/test_knowledge.py`, `tests/integration/test_repositories.py`.

- `Claim.evidence_ids` e `EvidenceRef.section_path` viraram tuplas — evidências
  de uma afirmação verificada e o caminho de seção de uma evidência não podem
  mais ser alterados;
- `Summary` agora é frozen com `supporting_passage_ids` em tupla — suportes de
  uma síntese publicada não podem ser removidos nem substituídos;
- `AnswerRunsRepository.save()` não atualiza mais colunas imutáveis: o UPDATE
  foi limitado à mesma allowlist de progresso do domínio (`session_id`, status,
  rewritten_query, inferred_filters, plan, candidates, selected_evidence_ids,
  response, verification, versions, latencies, error_code);
- antes do UPDATE, `save()` lê o registro existente e compara
  `question_original`, `question_anonymized`, `explicit_filters` e `created_at`;
  qualquer divergência levanta `ConflictError` tipado (nunca atualização
  silenciosa); ID inexistente segue como `NotFoundError`.

Testes obrigatórios (6/6 implementados):
1. `test_evidence_ids_are_immutable` (append em `Claim.evidence_ids`);
2. `test_section_path_is_immutable` (append/atribuição em `EvidenceRef.section_path`);
3. `test_supports_are_immutable_after_publication` (pop/atribuição em Summary);
4. `test_save_rejects_changed_question` (pergunta original e anonimizada);
5. `test_save_rejects_changed_explicit_filters`;
6. `test_save_persists_allowed_progress_fields` (rewritten_query, candidates,
   versions e latencies persistem e fazem round-trip com igualdade total).

## RRR02 — Temporário de sidecar interrompido ✅

Arquivos: `src/rag/infrastructure/artifacts.py`, `tests/unit/test_artifacts.py`.

- `_write_sidecar()` remove o temporário em `finally` (após `os.replace` o
  unlink é no-op; em falha, nada resta);
- `audit()` reconhece o padrão `<sha>.meta.<uuid>.tmp` via regex dedicada,
  remove o arquivo e o contabiliza em `AuditReport.sidecar_temps_removed` —
  nunca como objeto nem como hash corrompido;
- janela de crash (d) documentada no docstring do módulo.

Testes:
- `test_interrupted_sidecar_tmp_is_not_an_object` — reproduz o cenário do
  revisor (`<sha>.meta.deadbeef….tmp`): `objects=1`, `corrupted=[]`,
  `sidecar_temps_removed=1`, arquivo removido;
- `test_sidecar_write_failure_leaves_no_tmp` — falha simulada no `os.replace`
  do sidecar (monkeypatch): nenhum `.tmp` resta e o objeto publicado sem
  sidecar segue reparável (R04).

## RRR03 — Dimensão de embedding íntegra também no banco ✅

Arquivos: `0001_initial_schema.py`, `tests/integration/test_migrations.py`.

- `embedding_versions.dimensions` agora tem `CHECK (dimensions = 1024)` —
  inserção SQL direta ou outro adapter não registra dimensão incompatível com
  `vector(1024)`;
- erro tipado no repository (`EmbeddingDimensionError`) mantido — a constraint
  do banco é a última linha, não a única;
- paridade constante↔schema verificada por teste.

Testes:
- `test_embedding_versions_reject_incompatible_dimension_in_db` — INSERT direto
  com dimensão 8 falha com `CheckViolation`; dimensão 1024 passa;
- `test_schema_constant_matches_physical_column` — `format_type` da coluna
  `passages.embedding` é `vector(1024)` e igual a
  `infrastructure.schema.EMBEDDING_COLUMN_DIMENSIONS` e ao
  `EMBEDDING_DIMENSIONS` da migration (as três fontes amarradas por teste).

## Gates finais (2026-08-29, após RRR01–RRR03)

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK |
| `make lint` | OK (ruff + eslint) |
| `make format-check` | OK |
| `make typecheck` | OK (mypy strict 50 arquivos; tsc) |
| `make test` | 166 passed (backend) + 1 passed (frontend) |
| `make test-integration` | 48 passed (PostgreSQL real via testcontainers) |
| `make audit` | pip-audit: nenhuma vulnerabilidade; npm audit: 0 |
| `make security-scan` | nenhum IOC bloqueado |

---

# Resposta à quarta revisão (ROUND4)

Data: 2026-08-29
Referência: `docs/rag/REVIEW_T01_T04_ROUND4.md`
Resultado: **R4-01–R4-04 corrigidos**; nenhuma tarefa T05+ implementada. Sem
discordâncias técnicas — todos os achados foram aceitos e endereçados.

## R4-01 — Concorrência otimista em answer_runs (bloqueador) ✅

Arquivos: `0001_initial_schema.py`, `src/rag/domain/errors.py`, `runs.py`,
`infrastructure/repositories/runs.py`, `tests/integration/test_repositories.py`.

- coluna `revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0)` em
  `answer_runs` e campo `revision` em `AnswerRun` (fora da allowlist de
  `transition()` — só o repository o incrementa);
- `save()` faz compare-and-swap: `UPDATE ... SET ..., revision = revision + 1
  WHERE id = :id AND revision = :expected_revision RETURNING id`; zero linhas
  com ID existente vira `ConcurrencyError` tipado (subclasse de
  `ConflictError`, **distinta** de `NotFoundError`); o objeto retornado é
  relido do banco com a revisão nova;
- trigger `answer_runs_terminal_regression`: estado terminal
  (`succeeded`/`abstained`/`failed`/`cancelled`) nunca muda de status, mesmo
  via SQL direto ou outro adapter;
- o SELECT de campos imutáveis (RRR01) foi mantido — pergunta, filtros e
  criação divergentes seguem como `ConflictError`.

Testes obrigatórios (5/5 implementados) em `TestAnswerRunConcurrency`:
1. `test_stale_save_does_not_revert_succeeded` — reproduz o cenário do revisor
   (duas leituras em RUNNING; a segunda, stale, não reverte o SUCCEEDED);
2. `test_stale_save_does_not_revert_other_terminal_states` — ABSTAINED, FAILED
   e CANCELLED parametrizados;
3. `test_concurrent_conclusions_have_exactly_one_winner` — duas conclusões
   concorrentes: exatamente uma persiste;
4. `test_stale_update_does_not_drop_progress` — candidatos, latências e
   versões gravados pelo vencedor sobrevivem à tentativa stale;
5. `test_concurrency_error_is_distinct_from_not_found` — tipos distintos e
   NotFoundError preservado para ID inexistente.

Adicional: `test_db_rejects_terminal_status_regression` (trigger no banco) e
ajuste de `test_save_persists_allowed_progress_fields` para a revisão
incrementada (igualdade total contra o objeto relido).

## R4-02 — Proveniência de artefato derivado no banco ✅

Arquivos: `0001_initial_schema.py`, `tests/integration/test_repositories.py`.

- `editions` ganhou chave candidata `UNIQUE (id, source_sha256)`;
- `derived_artifacts` ganhou FK composta `(edition_id, derived_from_sha256)
  REFERENCES editions (id, source_sha256)` — o OCR só pode declarar como origem
  o hash original da própria edição.

Testes em `test_derived_provenance_enforced_by_db`: hash original errado →
FK violation; hash certo com edição errada → FK violation; associação válida
persiste.

## R4-03 — "Chapter é Section de topo" imposto ✅

Arquivos: `0001_initial_schema.py`, `src/rag/domain/knowledge.py`,
`tests/integration/test_repositories.py`.

- definição precisa: capítulo é `Section` com `level = 0` (seção de topo);
- trigger `summaries_chapter_top_level` (BEFORE INSERT/UPDATE) rejeita
  `scope_type='chapter'` cuja seção tem `level <> 0` — com mensagem clara;
  `section_id NULL` segue com o CHECK da tabela (o trigger não o intercepta);
- docstring de `Summary` atualizado: o domínio não carrega a seção, portanto a
  regra estrutural vive no banco; a pré-validação amigável caberá ao serviço
  de sínteses (T11).

Teste: `test_summary_chapter_scope_requires_top_level_section` — chapter para
seção aninhada (level=1) falha; os escopos válidos (incl. chapter com
level=0) seguem cobertos por `test_summary_valid_scopes_pass`.

## R4-04 — Descrição obsoleta da dimensão em EVIDENCE.md ✅

A seção antiga de T03 foi corrigida: `passages.embedding` é `vector(1024)`
fixo na revisão 0001, sem variável de ambiente (R02). Nenhuma outra ocorrência
de `RAG_EMBEDDING_DIMENSIONS` permanece no documento.

## Gates finais (2026-08-29, após R4-01–R4-04)

| Comando | Resultado |
|---------|-----------|
| `make lock` | OK |
| `make lint` | OK (ruff + eslint) |
| `make format-check` | OK |
| `make typecheck` | OK (mypy strict 50 arquivos; tsc) |
| `make test` | 166 passed (backend) + 1 passed (frontend) |
| `make test-integration` | 58 passed (PostgreSQL real via testcontainers) |
| `make audit` | pip-audit: nenhuma vulnerabilidade; npm audit: 0 |
| `make security-scan` | nenhum IOC bloqueado |
| `make audit` ∥ `make test` (concorrente) | ambos OK |
