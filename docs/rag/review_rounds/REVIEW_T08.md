# Revisão T08 — Busca lexical em português

Data: 2026-08-30
Branch revisada: `T08`
Commit da tarefa: `58ca161` (`Implement T08: Portuguese lexical search (FTS, phrase, trigram tolerance)`)
Estado revisado: `3cd9bb7` (`fix: merged T07`)
Resultado: **correções obrigatórias**

## Resumo executivo

T08 entrega uma base adequada para FTS em português: `LexicalQuery` tipada,
normalização por `portuguese_unaccent`, frase por `phraseto_tsquery`, termos
obrigatórios/excluídos, tolerância trigram configurável, filtros por edição e
obra, SQL parametrizado e testes de integração com PostgreSQL real.

Contudo, depois da integração de T06, a busca lexical não seleciona somente a
execução de indexação ativa. Em uma reindexação, o conjunto antigo é
preservado intencionalmente para reprodução, mas continua elegível para a
recuperação. Isso pode devolver texto, ranking e IDs de evidência obsoletos
ao fluxo atual. Portanto, T08 ainda não atende a separação entre histórico
reproduzível e índice corrente exigida pela versão ativa introduzida em T06.

## Bloqueador

### T8-01 — Busca lexical recupera passagens de `IndexRun` inativa

Arquivos:

- `backend/src/rag/infrastructure/repositories/search.py`;
- `backend/src/rag/application/index.py`;
- `backend/src/rag/infrastructure/repositories/index_runs.py`;
- `backend/tests/integration/test_lexical_search.py`.

T06 corrige a reindexação para preservar passagens antigas: quando cria uma
nova execução, `IndexingService.index_edition()` chama
`IndexRunsRepository.deactivate(active_run.id)` e persiste as novas passagens
com outro `index_run_id`. A migration `0003_index_runs` documenta que a
seleção do conjunto corrente é explícita e que há, no máximo, uma execução
`is_active` por edição.

Porém, a condição inicial de `LexicalSearchRepository.search()` é somente:

```python
conditions = ["p.embedding_version_id IS NOT NULL"]
```

e a consulta não faz `JOIN`/`EXISTS` em `index_runs`. Logo, todas as passagens
filhas de uma execução inativa satisfazem a busca exatamente como as da
execução ativa. Após `rag index <edition> --force`, uma consulta lexical pode
retornar ambos os conjuntos, inclusive a passagem/embedding/offset antigo,
ou ranquear o registro antigo antes do corrente. Isso viola a semântica de
índice ativo de T06 e impede que T08 seja uma fonte confiável para T09/RRF e
para evidências atuais.

Os testes de T08 não detectam o problema: `_seed()` cria passagens sem
`index_run_id`, e nenhum cenário executa duas indexações da mesma edição nem
inspeciona a exclusão de uma execução inativa.

Correção esperada:

- limitar a busca de produção às passagens cujo `index_run_id` referencia uma
  `index_runs.is_active`; definir explicitamente, e documentar, qualquer
  política de compatibilidade para linhas legadas `index_run_id IS NULL`, sem
  permitir que isso reintroduza conjuntos inativos;
- aplicar a mesma seleção antes de frase, FTS, trigram e filtros de
  obra/edição;
- adicionar uma integração que indexe a **mesma edição**, force uma segunda
  execução e prove que a busca não retorna nenhum `passage_id` da execução
  inativada; o teste deve preservar o histórico no banco e confirmar que a
  execução nova continua recuperável.

## Correções importantes

### T8-02 — Cobertura de filtros não prova as duas polaridades por dimensão

Arquivo: `backend/tests/integration/test_lexical_search.py`.

T08 testa `exclude_edition_ids` e `include_work_ids`, mas não testa
`include_edition_ids` nem `exclude_work_ids`. O repositório implementa as
quatro condições, mas a especificação exige filtros positivos e negativos por
obra/edição, e AC-07 trata exclusões como regra que não pode ser contornada.

Adicionar casos de integração para inclusão de edição e exclusão de obra,
preferencialmente combinados com uma consulta que também tenha candidatos
elegíveis, para provar que o predicado é aplicado no SQL e não apenas que uma
consulta sem resultado retorna lista vazia.

### T8-03 — `limit` não é validado e valor negativo remove o limite no PostgreSQL

Arquivo: `backend/src/rag/infrastructure/repositories/search.py`.

`limit` é repassado como parâmetro, o que evita injeção, mas não recebe
validação de faixa. Em PostgreSQL, `LIMIT -1` significa ausência de limite;
assim, um chamador do repositório pode recuperar todo o acervo, contrariando
o orçamento de candidatos que T09 deve controlar e criando risco de custo e
latência desnecessários. Validar um inteiro positivo e um máximo configurável
na fronteira apropriada, com testes para `0`, negativo e valor acima do teto.

## Evidências verificadas

| Comando | Resultado |
|---------|-----------|
| `make test-unit` | OK — 352 passed, 3 skipped |
| `make test-contract` | OK — 26 passed |
| `make lint` | OK |
| `make format-check` | OK |
| `make typecheck` | OK |
| `git diff --check` | OK |
| `cd backend && DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration/test_lexical_search.py -q` | OK — 14 passed |

Os testes de T08 existentes são úteis e exercitam PostgreSQL real: frase em
ordem contígua, acentos, stemming, conjunção, exclusão de termo, trigram,
passagem-pai, filtros, SQL hostil e lista vazia. Eles não cobrem T8-01 e não
substituem a prova de seleção da execução ativa.

## Julgamento

- AC-04: **atendido no escopo lexical** — há evidência de frase ordenada e
  contígua em português, com FTS e normalização de acentos.
- AC-07: **parcial** — filtros e exclusão de termo são aplicados no estágio
  lexical, mas a cobertura ainda não exercita inclusão/exclusão para ambas as
  dimensões e os estágios posteriores são responsabilidade de T09/T13.
- AC-15: **não atendido na integração T06→T08** — a recuperação atual pode
  selecionar passagens de uma versão de índice inativa, embora seu histórico
  deva permanecer apenas reproduzível.

T08 permanece bloqueada até T8-01 ser corrigido com integração contra duas
execuções da mesma edição. T8-02 e T8-03 devem ser resolvidos na mesma rodada
para completar o contrato de filtros e orçamento de recuperação.
