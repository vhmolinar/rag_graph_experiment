# Resposta à revisão T13 — rodada 2

Data: 2026-09-02
Referência: `docs/rag/review_rounds/REVIEW_T13_ROUND2.md`
Status: correção T13-R2-01 implementada e verificada.
Resultado anterior: **reprovado** (T13-01 e T13-02 corrigidos; T13-R2-01 crítico).

---

## T13-R2-01 (Crítico) — `AnswerBlock` sem `claim_id` ainda transporta fatos não verificados

### Diagnóstico
O reprodutor da revisão confirmaba que `AnswerBlock(text=" Marte tem duas
luas.", claim_id=None)` era aceito: a concatenação de blocos só provava que não
há texto fora da lista, não que o texto de um bloco nulo seja não factual. O
serviço enviaba só `answer.claims` ao verificador, deixando o bloco nulo sem
juízo.

### Correção (solução fechada sugerida pela revisão: tokens estruturais)
1. **Domínio** (`domain/answer.py`): `_is_structural_text(text)` exige que um
   bloco sem `claim_id` NÃO contenga caracteres alfabéticos
   (`not any(ch.isalpha() for ch in text)`). Um bloco nulo só pode contener
   whitespace, pontuação, símbolos e dígitos — tokens estruturais de Markdown
   controlados pelo servidor. Todo texto natural do modelo deve ser um bloco
   de afirmação idêntico a uma `Claim` (verbatim, como já exigido por T13-03).
   O reprodutor da revisão agora é rejeitado por construção (AC-09).
2. **Serviço** (`application/dissertative.py`): defensa em profundidade —
   `DissertativeService._revalidate_answer` revalida o contrato
   `GeneratedAnswer` do gerador antes de qualquer entrega; uma violação
   falha fechado (`ModelResponseError`), mesmo se um provedor contornasse os
   validators do domínio.
3. **Contrato de geração**: `_GENERATION_OUTPUT_CONTRACT` instrui o modelo que
   `claim_id=null` é SÓ para whitespace/pontuação/estrutura Markdown — nunca
   para texto natural, que deve ser uma afirmação com claim_id.

### Evidências
- `tests/unit/test_answer.py::TestGeneratedAnswer::test_null_block_with_factual_prose_is_rejected`
  — reprodutor da revisão ("Marte tem duas luas." em bloco nulo) rejeitado;
- `tests/unit/test_answer.py::TestGeneratedAnswer::test_null_block_with_natural_prose_is_rejected`
  — prosa conectiva natural ("Portanto, conclúse.") também rejeitada;
- `tests/unit/test_answer.py::TestGeneratedAnswer::test_structural_null_blocks_allowed`
  — whitespace/pontuação/dígitos/Markdown permanecem válidos;
- `tests/unit/test_dissertative.py::TestAnswerMarkdownBinding::test_service_rejects_generator_prose_outside_claims`
  — injeção de resposta inválida via `model_construct` → `ModelResponseError`;
  nada é entregue ao cliente;
- `tests/unit/test_generation_adapter.py::TestGenerate::test_null_block_with_factual_prose_raises_model_response_error`
  — o adapter de geração falha fechado no contrato.

Reproductor da revisão reexecutado:

```sh
cd backend && uv run python -c 'from uuid import uuid4; from rag.domain.answer import AnswerBlock, Claim, GeneratedAnswer; claim=Claim(id="c1", text="A fonte diz X.", evidence_ids=(uuid4(),)); blocks=(AnswerBlock(text=claim.text, claim_id="c1"), AnswerBlock(text=" Marte tem duas luas.", claim_id=None)); GeneratedAnswer(answer_markdown="".join(b.text for b in blocks), blocks=blocks, claims=(claim,), limitations=(), abstained=False)'
```

Resultado:

```text
Value error, bloco sem claim_id só pode contener tokens estruturais
(whitespace/pontuação/Markdown) — todo texto natural deve ser uma afirmação
verificada (T13-R2-01)
```

---

## Revalidação dos achados anteriores

| Achado | Situação |
|---|---|
| T13-01 | Corrigido (inalterado nesta rodada). |
| T13-02 | Corrigido (inalterado nesta rodada). |
| T13-03 | Corrigido integralmente com T13-R2-01: a ligação Markdown↔claims é fechada também para blocos nulos. |

---

## Evidências executadas (2026-09-02, Linux; PostgreSQL real via testcontainers sobre podman)

Ambiente: `DOCKER_HOST=unix:///run/user/1000/podman/podman.sock`,
`TESTCONTAINERS_RYUK_DISABLED=true`.

| Comando | Resultado |
|---|---|
| `uv run ruff check src tests` | OK — All checks passed |
| `uv run ruff format --check src tests` | OK — 116 arquivos |
| `uv run mypy src tests` | OK — 116 arquivos, strict, 0 issues |
| `uv run pytest tests/unit -q` | OK — 563 passed, 3 skipped (e2e opcionais) |
| `uv run pytest tests/integration/test_dissertative_pipeline.py -q` | OK — 7 passed (PostgreSQL real) |
| `uv run pytest tests/integration -q` | OK — 218 passed, 1 skipped (PostgreSQL real) |
| Reproductor da revisão (T13-R2-01) | rejeitado por construção (ver acima) |

Arquivos alterados:
- `backend/src/rag/domain/answer.py` (T13-R2-01)
- `backend/src/rag/application/dissertative.py` (T13-R2-01)
- `backend/tests/fixtures/model_doubles.py`
- `backend/tests/unit/test_answer.py`, `test_dissertative.py`,
  `test_generation_adapter.py`, `test_runs.py`
- `backend/tests/integration/test_dissertative_pipeline.py`,
  `test_repositories.py`
- `docs/rag/NOTES.md`, `docs/rag/EVIDENCE.md`

## Conclusão

O bloqueador T13-R2-01 foi corrigido pela solução fechada da própria revisão
(tokens estruturais) e verificado com testes adversariais no domínio, no
serviço e no adapter de geração, além de testes unitários (563 passed) e de
integração contra PostgreSQL real (218 passed, 1 skipped). Não restam achados
bloqueadores no escopo de T13; AC-09 fica coberto. A revisão pode ser refeita.
