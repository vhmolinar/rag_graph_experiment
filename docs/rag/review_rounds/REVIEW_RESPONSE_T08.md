# Resposta à revisão T08 — Busca lexical em português

Data: 2026-08-30
Referência: `docs/rag/REVIEW_T08.md`
Status: correções T8-01 (bloqueador), T8-02 e T8-03 implementadas e verificadas.
Resultado anterior: **correções obrigatórias** (AC-15 não atendido na integração T06→T08; AC-07 parcial).

## T8-01 (bloqueador) — Busca lexical recuperava passagens de `IndexRun` inativa

Corrigido. `LexicalSearchRepository.search()` agora seleciona explicitamente o
conjunto corrente — a execução de indexação ATIVA — em vez de considerar toda
passagem com `embedding_version_id`:

```sql
AND (
  EXISTS (SELECT 1 FROM index_runs ir WHERE ir.id = p.index_run_id AND ir.is_active)
  OR (p.index_run_id IS NULL AND NOT EXISTS (
        SELECT 1 FROM index_runs ir2
        WHERE ir2.edition_id = p.edition_id AND ir2.is_active
      ))
)
```

A condição vive no `WHERE` do `passages`, portanto se aplica **antes** de
frase, FTS, trigram e dos filtros de obra/edição (TASKS.md T08/T08; SPEC §8.4).
Não é uma exclusão física: o histórico continua no banco para reprodução
(SPEC §6/AC-15), apenas deixa de ser candidato.

**Política de compatibilidade para linhas legadas (`index_run_id IS NULL`)**
— documentada no docstring de `search.py` e em `NOTES.md` §10.9 item 8:

- `index_run_id IS NULL` marca linhas fora do fluxo de `rag index` (fixtures de
  teste, acervo anterior à indexação). Elas permanecem elegíveis **somente**
  enquanto a edição não possui execução ativa;
- assim que a edição é indexada (`index_runs` com `is_active`), essas linhas
  deixam de ser candidatas, junto com as de execuções antigas. A política
  **nunca** reintroduz um conjunto inativo.

**Evidência principal** (`tests/integration/test_lexical_search.py`,
PostgreSQL real):

- `test_search_only_returns_passages_of_active_index_run` — reproduz o estado
  após `rag index <edition> --force`: a MESMA edição com duas execuções
  (`IndexRunsRepository.create` da execução 1 ativa → passagens dela →
  `deactivate(run1)` → cria run2 ativa → passagens dela), ambas contendo o
  termo "ciúme". A busca devolve apenas a passagem da execução **ativa**;
  a da execução inativada nunca retorna; e o teste confirma que as **duas**
  passagens continuam no banco (histórico preservado) com **uma única**
  execução ativa.
- `test_legacy_rows_stay_eligible_until_edition_has_active_run` — as linhas
  legadas (`index_run_id IS NULL`) do corpus são recuperáveis enquanto a edição
  não tem execução ativa, e deixam de ser candidatas assim que a edição ganha
  uma execução ativa.

O lado da indexação (histórico preservado + uma execução ativa por edição,
`IndexingService` + `IndexRunsRepository.deactivate`) já era provado por
`tests/integration/test_index.py::test_force_reindexes_preserves_passage_history`.
O teste acima cobre exatamente o vácuo apontado: a recuperação precisava
respeitar essa separação. Nota de execução: `_seed()` de T08 passou a conviver
com a política legada (edição sem execução ativa → linhas NULL continuam
elegíveis), então todos os 14 testes pré-existentes seguem passando sem
reindexação manual, e os novos cobrem o caso com duas execuções.

## T8-02 (importante) — Cobertura de filtros não provava as duas polaridades

Adicionados em `tests/integration/test_lexical_search.py`:

- `test_filter_include_edition` — `include_edition_ids` com candidatos
  elegíveis em **ambas** as edições ("liberdade" aparece em A e em B): só a
  edição incluída retorna (`ciume_accented` sim, `liberdade_b` não).
- `test_filter_exclude_work` — `exclude_work_ids` com candidatos da obra
  excluída presentes ("liberdade" em A e em B): a obra excluída nunca retorna
  (`ciume_accented` não, `liberdade_b` sim) — a exclusão não é contornada por
  um candidato elegível.

Os casos foram desenhados para provar que o predicado é aplicado no SQL (a
busca tem resultados possíveis nas duas polaridades), não que uma consulta sem
resultado devolve lista vazia (fraqueza apontada para `test_filter_by_work`).

## T8-03 (importante) — `limit` não validado

`search()` agora valida `1 <= limit <= MAX_SEARCH_LIMIT` na fronteira do
repository (constante de módulo `MAX_SEARCH_LIMIT = 100`; default do parâmetro
segue 20), levantando `ValueError` antes de qualquer SQL. Motivo documentado
em `NOTES.md` §10.9 item 9: no PostgreSQL `LIMIT -1` = ausência de limite e
`LIMIT 0` não é erro — um chamador descuidado recuperaria o acervo inteiro,
violando o orçamento de candidatos que T09 controla. O teto é o "ponto que
deve ser calibrado" de `NOTES.md` §4 (quantidade de candidatos lexical); se o
benchmark de T19 indicar outro valor, ajusta-se a constante (nenhuma migration
nem schema mudou).

Evidências: `test_limit_is_respected` (limite é um teto real — `limit=1`
devolve exatamente 1) e `test_invalid_limit_is_rejected` (parametrizado sobre
`0`, `-1` e `MAX_SEARCH_LIMIT + 1`).

## Verificações finais

| Comando | Resultado |
|---------|-----------|
| `cd backend && uv run pytest tests/integration/test_lexical_search.py -q` (Docker/Podman) | OK — 22 passed |
| `cd backend && uv run pytest tests/unit tests/contract -q` | OK — 378 passed, 3 skipped |
| `cd backend && uv run pytest tests/integration -q` (Docker/Podman) | OK — 138 passed, 1 skipped |
| `make lint` | OK (ruff + eslint) |
| `make format-check` | OK (ruff format + prettier) |
| `make typecheck` | OK (mypy strict 86 arquivos; tsc) |
| `git diff --check` | OK |

Matriz atualizada em `docs/rag/EVIDENCE.md` (AC-04, AC-07, AC-15). Nenhuma
dependência foi adicionada, nenhuma migration criada, e nenhum requisito ou
critério de aceitação foi alterado. Não foram criados commits nem abertas
pull/merge requests até este ponto.
