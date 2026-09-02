# Segunda revisão T08 — Validação da resposta às correções

Data: 2026-08-30
Branch revisada: `T08`
Commit revisado: `f80b0f9` (`Fix T08 review findings T8-01 through T8-03 (active index run, filters, limit)`)
Resposta avaliada: `docs/rag/REVIEW_RESPONSE_T08.md`
Resultado: **correção obrigatória remanescente**

## Resumo executivo

A resposta corrige integralmente T8-01 e T8-02. A busca agora seleciona a
`IndexRun` ativa e possui uma política explícita para passagens legadas; os
testes de integração reproduzem duas execuções da mesma edição e confirmam
que o histórico é preservado sem voltar a ser candidato. Os quatro filtros
por obra/edição também passaram a ter evidência nas duas polaridades.

T8-03, porém, só valida a faixa numérica de `limit`; não valida que ele seja
um inteiro em runtime. Isso diverge da resposta, que afirma validar “um
inteiro positivo”, e deixa a fronteira do repositório falhar tarde para tipos
inválidos.

## Correção obrigatória remanescente

### R2-T8-03 — `limit` aceita `float` e `bool` apesar do contrato de inteiro

Arquivo:

- `backend/src/rag/infrastructure/repositories/search.py`.

O código atual é:

```python
if limit < 1 or limit > MAX_SEARCH_LIMIT:
    raise ValueError(...)
```

Anotações de tipo não são validação em runtime no Python. Portanto, `1.5`
passa por ambas as comparações e `True` também passa, pois `bool` é subtipo
de `int` e equivale a `1`. Ambos chegam à montagem/execução SQL, em vez de
falharem com o `ValueError` tipado e sanitizado prometido pela resposta.

Evidência reproduzida sem acessar o banco, usando a fronteira do repository:

```text
1.5: passed validation
True: passed validation
```

Isso não reabre o problema anterior de `LIMIT -1`, já corrigido, mas impede
considerar T8-03 concluída: a função anuncia e documenta validação de inteiro
antes de SQL, enquanto aceita valores que não são inteiros. Dependendo da
adaptação de parâmetro do driver/PostgreSQL, o resultado será erro tardio ou
uma semântica de limite não intencional.

Correção esperada:

- rejeitar explicitamente qualquer valor cujo tipo não seja `int`, incluindo
  `bool`, antes das comparações de faixa; manter `1 <= limit <=
  MAX_SEARCH_LIMIT`;
- ampliar `test_invalid_limit_is_rejected` para `1.5`, `True` e, se o
  contrato aceitar entradas externas nessa camada, uma string numérica;
- provar que a rejeição ocorre antes de obter cursor/executar SQL.

## Correções aceitas

### T8-01 — Seleção da execução ativa

Aceita. O predicado correlacionado por `index_run_id`/`is_active` exclui
passagens de execuções inativas. A alternativa explícita para
`index_run_id IS NULL` só admite uma linha legada enquanto sua edição não
possui execução ativa, portanto não reintroduz histórico inativo.

`test_search_only_returns_passages_of_active_index_run` cria duas
`IndexRun`s da mesma edição, inativa a primeira e prova que só a passagem da
segunda retorna, enquanto ambas continuam persistidas. O teste complementar
confirma a política de compatibilidade legada.

### T8-02 — Polaridades de filtros

Aceita. `test_filter_include_edition` e `test_filter_exclude_work` usam
candidatos elegíveis nos dois lados do filtro; junto com os testes anteriores
de exclusão de edição e inclusão de obra, há cobertura de inclusão e exclusão
para obra e edição no SQL lexical.

## Evidências verificadas

| Comando | Resultado |
|---------|-----------|
| `cd backend && DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration/test_lexical_search.py -q` | OK — 22 passed |
| `cd backend && uv run ruff check src tests` | OK |
| `cd backend && uv run ruff format --check src tests` | OK — 86 arquivos |
| `cd backend && uv run mypy src tests` | OK — 86 arquivos, sem issues |
| `cd backend && uv run pytest tests/unit tests/contract -q` | OK — 378 passed, 3 skipped |
| verificação direta da fronteira com `limit=1.5` e `limit=True` | ambos passaram pela validação; falha descrita acima |

## Julgamento

- AC-04: **atendido no escopo lexical**.
- AC-07: **parcial conforme o cronograma** — o estágio lexical está coberto;
  vetorial/RRF e geração seguem para T09/T13.
- AC-15: **atendido na integração T06→T08** — a recuperação seleciona somente
  o conjunto de índice corrente, preservando o histórico para reprodução.

T08 permanece bloqueada somente por R2-T8-03. Após validar o tipo de `limit`
em runtime e adicionar os testes de regressão, as correções da revisão T08
estarão integralmente aceitas.
