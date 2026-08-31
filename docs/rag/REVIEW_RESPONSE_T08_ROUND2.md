# Resposta à segunda revisão T08 — R2-T8-03 (`limit` com tipo inválido)

Data: 2026-08-30  
Referência: `docs/rag/REVIEW_T08_ROUND2.md`  
Status: correção R2-T8-03 implementada e verificada.  
Resultado anterior: T8-01 e T8-02 aceitos; T8-03 pendente por R2-T8-03.

## R2-T8-03 — `limit` aceitava `float` e `bool`

Real. Anotação de tipo não é validação em runtime no Python: `1.5` passa nas
duas comparações de faixa e `True` é `int` (`bool` é subtipo) com valor `1`.
Ambos chegavam à montagem/execução SQL, contrariando o contrato de "inteiro
validado antes do SQL".

Correção em `LexicalSearchRepository.search()` —
`backend/src/rag/infrastructure/repositories/search.py`:

```python
if isinstance(limit, bool) or not isinstance(limit, int):
    raise ValueError(
        "limit deve ser um inteiro: valores bool e float são rejeitados; "
        "uma string numérica também não é aceita."
    )
if limit < 1 or limit > MAX_SEARCH_LIMIT:
    raise ValueError(
        f"limit deve ser um inteiro entre 1 e {MAX_SEARCH_LIMIT} (recebido {limit}) — "
        "valor não-positivo ou acima do teto viola o orçamento de candidatos."
    )
```

Pontos atendidos:

- **Tipo validado em runtime antes das comparações de faixa**, no início do
  método, antes de qualquer acesso a banco: `bool` (subtipo de `int`) e
  `float` (inclusive `1.5`) são rejeitados explicitamente; uma string
  numérica também é rejeitada (mesmo sendo coercível), pois esta fronteira
  consome apenas inteiros internos (T09), não entrada de usuário.
- **A manutenção de `1 <= limit <= MAX_SEARCH_LIMIT`** permanece, com o
  `ValueError` tipado e sanitizado para faixa.
- O docstring do módulo (T8-03) foi mantido e a nova regra de tipo é
  comentada no próprio código.

Evidências (regressões novas em `tests/integration/test_lexical_search.py`):

- `test_invalid_limit_is_rejected` — agora parametrizado sobre `0`, `-1`,
  `MAX_SEARCH_LIMIT + 1`, `1.5`, `True` e uma string numérica (`"3"`),
  todos com `pytest.raises(ValueError, match="limit deve ser um inteiro")`;
- `test_invalid_limit_rejected_before_obtaining_cursor` — usa um
  `MagicMock` em que `conn.cursor` nunca é acessado: a rejeição de `True`
  ocorre antes de obter cursor/executar SQL (prova de que não é erro tardio
  do driver nem semântica de limite não intencional).

Verificação direta da reprodução da revisão (fronteira do repository):

```text
limit=1.5  -> ValueError: limit deve ser um inteiro: ...
limit=True -> ValueError: limit deve ser um inteiro: ...
```

## Verificações finais

| Comando | Resultado |
|---------|-----------|
| `cd backend && uv run pytest tests/integration/test_lexical_search.py -q` (Docker/Podman) | OK — 26 passed (antes: 22) |
| `cd backend && uv run pytest tests/unit tests/contract -q` | OK — 378 passed, 3 skipped |
| `cd backend && uv run pytest tests/integration -q` (Docker/Podman) | OK — 142 passed, 1 skipped (antes: 138) |
| `make lint` | OK (ruff + eslint) |
| `make format-check` | OK (ruff format + prettier) |
| `make typecheck` | OK (mypy strict 86 arquivos; tsc) |
| `git diff --check` | OK |

Matriz atualizada em `docs/rag/EVIDENCE.md` (T8-03 e comandos). Nenhuma
dependência foi adicionada, nenhuma migration criada, e nenhum requisito ou
critério de aceitação foi alterado. Não foram criados commits nem abertas
pull/merge requests até este ponto.
