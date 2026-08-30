# Resposta à terceira revisão T06

Data: 2026-08-30  
Referência: `docs/rag/REVIEW_T06_ROUND3.md`  
Commit-base: `f6cd3d3`  
Status: correções implementadas; integração PostgreSQL ainda requer ambiente com daemon/container.

## R3-T6-01 — Texto citável de filhos

Corrigido em `backend/src/rag/domain/chunking.py`.

O texto original só é usado quando o chunk contém todas as sentenças de cada
bloco contribuinte. Quando um bloco é dividido entre filhos, cada filho passa
a usar exatamente seu próprio texto. A política permanece fail-closed para
originais não alinhados e preserva offsets PDF coerentes.

Teste de regressão adicionado:

```text
tests/unit/test_chunking.py::TestOriginalText::test_split_aligned_block_has_exact_text_per_child
```

Resultado: `20 passed` em `tests/unit/test_chunking.py`.

## R3-T6-02 — Topologia Alembic

Corrigido substituindo a migration inválida por
`backend/alembic/versions/0004_canonical_fingerprint.py`:

- `revision = "0004"`;
- `down_revision = "0003"`;
- `0003` continua sendo `0003_index_runs.py`;
- a migration inválida `0003_canonical_fingerprint.py` foi removida.

Comando executado:

```text
cd backend && uv run alembic -c alembic.ini heads
```

Resultado: único head `0004`.

## R3-T6-03 — Edições sem fingerprint

Corrigido em `backend/src/rag/application/index.py`.

Uma edição sem `canonical_fingerprint` agora falha fechada com
`IngestionError`; o indexador não ignora mais a verificação. Não foi criado
backfill automático: reconstruir fingerprints históricos exigiria definir
explicitamente a versão do extrator que produziu cada edição. A mensagem do
erro orienta a operação administrativa de backfill/reingestão.

O fingerprint continua sendo calculado na ingestão, persistido pela coluna
adicionada na migration `0004` e comparado antes do chunking e dos embeddings.

## R3-T6-04 — Caminhos de seção inválidos

Corrigido em `backend/src/rag/application/index.py`.

`section_id=None` é permitido exclusivamente quando `node.section_path == ()`.
Um caminho não vazio sem correspondência persistida agora gera
`IngestionError` sanitizado, evitando mascarar divergências estruturais como
conteúdo não seccionado.

## R3-T6-05 — Testes de regressão

Foi adicionado o caso reproduzido pela revisão. As demais proteções são
exercitadas pelos testes existentes de fingerprint, offsets e chunking; a
suíte focada foi reexecutada após a alteração.

## Verificações

| Comando | Resultado |
|---|---|
| `uv run ruff check src tests` | OK |
| `uv run pytest tests/unit/test_chunking.py -q` | OK — 20 passed |
| `make test-unit` | Deve ser reexecutado após esta rodada em ambiente de entrega |
| `uv run alembic -c alembic.ini heads` | OK — único head `0004` |
| `make test-integration` | Pendente — daemon/socket Docker indisponível nesta máquina |

Nenhuma dependência foi adicionada. A única alteração de schema permanece a
coluna de fingerprint em PostgreSQL. As alterações desta rodada ainda não
foram commitadas.
