# Resposta à revisão T12

Data: 2026-09-01
Referência: `docs/rag/review_rounds/REVIEW_T12.md`
Status: correções T12-01 a T12-03 implementadas e verificadas.
Resultado anterior: **reprovado** (T12-01 bloqueador; T12-02 médio; T12-03 baixa).

---

## T12-01 (Bloqueador) — Citações multipágina não são reproduzíveis

### Diagnóstico
`EvidenceRef` expõe somente `physical_page`, um único `printed_label` e um par
de offsets; `get_citable` lê apenas `page_start_id`. Para um chunk que começa
na página A e termina na B, `char_end` é relativo à página B mas o cliente
recebe apenas A — `page_A[char_start:char_end]` é incorreto e a citação não se
abre/destaca/recomponhe (AC-03).

### Correção
1. **Domínio** (`domain/answer.py`, `domain/context.py`): `EvidenceRef` e
   `CitablePassage` passaram a transportar início E fim da localização:
   - `page_start_id`/`page_end_id` (IDs das páginas);
   - `physical_page`/`page_end` (índices físicos; `physical_page` continua
     sendo a página de início, compatível com o contrato anterior);
   - `printed_label`/`printed_end_label` (rótulos impressos);
   - `char_start` relativo à `physical_page` e `char_end` relativo à
     `page_end` — mesmo contrato do chunker (NOTES.md §10.6 item 3).
   Validator novo: `page_end >= physical_page` quando ambos presentes.
2. **Persistência** (`infrastructure/repositories/passages.py`): `get_citable`
   inclui o JOIN da página de fim (`pend`) e projeta os campos novos.
3. **Testes**:
   - Unit: projeção multipágina em `select_evidences`
     (`test_context.py::test_multipage_metadata_projected_on_evidence`) e
     contrato de `EvidenceRef` (`test_answer.py::test_multipage_reference_carries_end_page`,
     `test_page_end_must_not_precede_page_start`).
   - Integração (`test_context_pipeline.py::test_quote_multipage_passage_reproduces_text`):
     seed com uma passagem que atravessa duas páginas; `quote` devolve
     `physical_page=0`, `page_end=1`, rótulos "p. 1"/"p. 2", IDs das duas
     páginas e offsets por página; a abertura da origem reproduz o trecho
     exato e os offsets destacam dentro do texto de cada página.

### Evidências
- `tests/unit/test_context.py::TestSelectEvidences::test_multipage_metadata_projected_on_evidence`
- `tests/unit/test_answer.py::TestEvidenceRef::test_multipage_reference_carries_end_page`
- `tests/integration/test_context_pipeline.py::test_quote_multipage_passage_reproduces_text`
  — reconstrução `page_start.text[char_start:] + "\n" + page_end.text[:char_end]`
  e destaque por página, contra PostgreSQL real.

---

## T12-02 (Médio) — Diversidade por conceito foi omitida

### Diagnóstico
`select_evidences` só diversificava por `edition_id`; não havia associação
candidato→conceito nem teste da dimensão conceitual. Uma resposta conceitual
podía consumir todo o orçamento em passagens de um único conceito.

### Correção
1. **Associação rastreável passagem→conceito**: a tabela `concept_evidence`
   (T11) já mapeia passagens a conceitos; `get_citable` agora a carrega
   (`CitablePassage.concepts: tuple[str, ...]` — rótulos normalizados, ordenados).
2. **Seleção** (`select_evidences`): SÓ quando `needs_diversity`, os candidatos
   que traem um conceito ainda não seleccionado são preferidos sobre os que
   repetem conceitos já cobertos (duas passadas, segunda na ordem do ranking).
   Sem quota cega: nunca se preenche — um conceito com poucos candidatos
   simplesmente não preenche o orçamento (limite flexível em os dois eixos:
   edição e conceito).
3. **Testes**:
   - Unit (`test_context.py`): `test_concept_diversity_changes_selection`
     ([c0,c1,c2] → com diversidade [c0,c2,c1]) e
     `test_concept_diversity_is_flexible_not_quota` (sem preenchimento).
   - Integração (`test_context_pipeline.py::test_concept_diversity_changes_selection_in_pipeline`):
     conceitos "liberdade" (a0/a1) e "destino" (b0); sem diversidade a seleção
     é [a1,a0,b0], com diversidade [a1,b0,a0] — a diversificação por conceito
     altera a seleção no pipeline completo (recuperação + montagem).

### Evidências
- `tests/unit/test_context.py::TestSelectEvidences::test_concept_diversity_changes_selection`
- `tests/unit/test_context.py::TestSelectEvidences::test_concept_diversity_is_flexible_not_quota`
- `tests/integration/test_context_pipeline.py::test_concept_diversity_changes_selection_in_pipeline`

---

## T12-03 (Baixa) — Teste de “generator não chamado” não observa o sistema sob teste

### Diagnóstico
`test_quote_never_calls_generator` instanciava `FakeGeneratorProvider` sem o
injetar em `_quote`/`ContextService`/nenhuma dependência; a asserção apenas
verificava a lista recém-criada — não observava o fluxo de uma consulta.

### Correção
Reformulado como **verificação estrutural** (`test_quote_has_no_generation_path`):
- remove o `FakeGeneratorProvider` e a asserção de spy;
- verifica que nem `ContextService.quote` nem `assemble` aceitam provedor de
  geração (assinatura — a ausência é estructural, é a proteção honesta que
  existe hoje);
- verifica que a resposta `quote` contém só trechos literais do acervo.
- Limitação documentada (NOTES.md §14 item 3, EVIDENCE.md T12): quando a
  orquestração de consultas existir (T13), injetar um generator que falhe ao
  ser chamado e exercitar o fluxo `quote` completo.

### Evidências
- `tests/integration/test_context_pipeline.py::test_quote_has_no_generation_path`

---

## Correção adicional — seed de integração com EmbeddingVersion incompatível

Ao executar a integração PostgreSQL (condição mínima da revisão), reproduzimos
que o seed de `test_context_pipeline.py` criava uma `EmbeddingVersion`
(`label="emb-ctx"`) distinta da do provedor de recuperação
(`ConceptEmbeddingProvider.embedding_version`, `label="concept-embedding"`). O
estágio vetorial filtra por `embedding_version_id` (T9-01), resultando em zero
candidatos vetoriais e falhas de ordem (`test_quote_snapshot_with_text_and_metadata`
etc.). O seed passou a usar `provider.embedding_version` — mesmo padrão de
`test_retrieval_pipeline.py`. Esta era a causa das 5 falhas de ordem que
impediriam a integração de servir como evidência.

Evidência: a suíte `tests/integration/test_context_pipeline.py` passa
integra (10 passed) após a correção.

---

## Evidências executadas (2026-09-01, Linux; PostgreSQL real via testcontainers sobre podman)

Ambiente: `DOCKER_HOST=unix:///run/user/1000/podman/podman.sock`,
`TESTCONTAINERS_RYUK_DISABLED=true`.

| Comando | Resultado |
|---|---|
| `git diff --check HEAD` | passou |
| `uv run ruff check src tests` | OK — All checks passed |
| `uv run ruff format --check src tests` | OK — 109 arquivos |
| `uv run mypy src tests` | OK — 109 arquivos, strict, 0 issues |
| `uv run pytest tests/unit -q` | OK — 493 passed, 3 skipped (e2e opcionais) |
| `uv run pytest tests/integration/test_context_pipeline.py -q` | OK — 10 passed (PostgreSQL real) |
| `uv run pytest tests/integration -q` | OK — 211 passed, 1 skipped (PostgreSQL real) |

Arquivos alterados:
- `backend/src/rag/domain/answer.py` (T12-01)
- `backend/src/rag/domain/context.py` (T12-01, T12-02)
- `backend/src/rag/infrastructure/repositories/passages.py` (T12-01, T12-02)
- `backend/tests/unit/test_answer.py`, `backend/tests/unit/test_context.py` (T12-01, T12-02)
- `backend/tests/integration/test_context_pipeline.py` (T12-01, T12-02, T12-03; seed)
- `docs/rag/NOTES.md`, `docs/rag/EVIDENCE.md`

## Conclusão

O bloqueador T12-01, o achado médio T12-02 e o achado baixa T12-03 de
REVIEW_T12.md foram corrigidos e verificados com testes de integração contra
PostgreSQL real (211 passed, 1 skipped), executados de facto no fluxo real de
recuperação + montagem de contexto. A matriz `AC-01` a `AC-20` foi atualizada
em `docs/rag/EVIDENCE.md` (AC-03, AC-08, AC-11, AC-15). A revisão pode ser
refeita.
