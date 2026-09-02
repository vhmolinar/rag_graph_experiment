# Resposta à revisão T13 — rodada 3

Data: 2026-09-02
Referência: `docs/rag/review_rounds/REVIEW_T13_ROUND3.md`
Status: correção T13-R3-01 implementada e verificada.
Resultado anterior: **reprovado** (T13-01, T13-02 e T13-R2-01 corrigidos; T13-R3-01 crítico).

---

## T13-R3-01 (Crítico) — a categoria “estrutural” permite afirmações numéricas não verificadas

### Diagnóstico
O reprodutor da rodada 3 confirmaba que `AnswerBlock(text=" 2024",
claim_id=None)` era aceito: `_is_structural_text` (rodada 2) classificava como
"estrutural" todo texto sem caracteres alfabéticos, permitendo números,
datas, quantidades e símbolos num bloco sem claim. O verificador recebia só
`answer.claims`; o número não era julgado.

### Correção (gramática realmente estrutural: só whitespace)
1. **Domínio** (`domain/answer.py`): `_is_structural_text` foi substituída por
   `_is_whitespace_text(text) = text.isspace()`. Um bloco sem `claim_id` só
   pode contener whitespace — separadores/parágrafos inseridos pelo renderer.
   NINGÚN conteúdo semântico do modelo (texto, números, datas, quantidades,
   URLs, emoji, símbolos) pode existir fora de uma `Claim` verificada (AC-09;
   checklist §12). O reprodutor " 2024" é rejeitado por construção.
2. **Serviço** (`application/dissertative.py`): a defensa em profundidade
   (`_revalidate_answer`) usa o mesmo validator — conteúdo numérico fora das
   claims falha fechado (`ModelResponseError`) antes de qualquer entrega.
3. **Contrato de geração**: `_GENERATION_OUTPUT_CONTRACT` declara
   `claim_id=null` SÓ para whitespace; formatação é inserida pelo renderer.

### Evidências
- `tests/unit/test_answer.py::TestGeneratedAnswer::test_null_block_with_semantic_content_is_rejected`
  — parametrizado: ano (" 2024", o reprodutor da rodada 3), quantidade
  (" 42"), porcentagem (" 12.5%"), data (" 3 de maio"), URL
  (" https://exemplo.com") — todos rejeitados;
- `tests/unit/test_answer.py::TestGeneratedAnswer::test_whitespace_null_blocks_allowed`
  — whitespace puro permanece válido;
- `tests/unit/test_dissertative.py::TestAnswerMarkdownBinding::test_service_rejects_generator_numeric_content_outside_claims`
  — injeção via `model_construct` com " 2024" → `ModelResponseError`; nada é
  entregue ao cliente;
- `tests/unit/test_generation_adapter.py::TestGenerate::test_null_block_with_numeric_content_raises_model_response_error`
  — o adapter de geração falha fechado no contrato.

Reproductor da rodada 3 reexecutado:

```sh
cd backend && uv run python -c 'from uuid import uuid4; from rag.domain.answer import AnswerBlock, Claim, GeneratedAnswer; claim=Claim(id="c1", text="A fonte contém uma data.", evidence_ids=(uuid4(),)); blocks=(AnswerBlock(text=claim.text, claim_id="c1"), AnswerBlock(text=" 2024", claim_id=None)); GeneratedAnswer(answer_markdown="".join(b.text for b in blocks), blocks=blocks, claims=(claim,), limitations=(), abstained=False)'
```

Resultado:

```text
Value error, bloco sem claim_id só pode contener whitespace — todo conteúdo
semântico (texto, números, datas, quantidades) deve ser uma afirmação
verificada (T13-R3-01)
```

---

## Revalidação dos achados anteriores

| Achado | Situação |
|---|---|
| T13-01 | Corrigido (inalterado nesta rodada). |
| T13-02 | Corrigido (inalterado nesta rodada). |
| T13-03 | Corrigido integralmente com T13-R2-01 e T13-R3-01: a ligação Markdown↔claims é fechada também para blocos nulos, inclusive contra conteúdo numérico. |

---

## Evidências executadas (2026-09-02, Linux; PostgreSQL real via testcontainers sobre podman)

Ambiente: `DOCKER_HOST=unix:///run/user/1000/podman/podman.sock`,
`TESTCONTAINERS_RYUK_DISABLED=true`.

| Comando | Resultado |
|---|---|
| `uv run ruff check src tests` | OK — All checks passed |
| `uv run ruff format --check src tests` | OK — 116 arquivos |
| `uv run mypy src tests` | OK — 116 arquivos, strict, 0 issues |
| `uv run pytest tests/unit -q` | OK — 570 passed, 3 skipped (e2e opcionais) |
| `uv run pytest tests/unit/test_answer.py tests/unit/test_dissertative.py tests/unit/test_generation_adapter.py tests/unit/test_verification.py tests/unit/test_verifier_adapter.py -q` | OK — 108 passed |
| `uv run pytest tests/integration/test_dissertative_pipeline.py -q` | OK — 7 passed (PostgreSQL real) |
| `uv run pytest tests/integration -q` | OK — 218 passed, 1 skipped (PostgreSQL real) |
| Reproductor da rodada 3 (T13-R3-01) | rejeitado por construção (ver acima) |

Arquivos alterados:
- `backend/src/rag/domain/answer.py` (T13-R3-01)
- `backend/src/rag/application/dissertative.py` (T13-R3-01)
- `backend/tests/unit/test_answer.py`, `test_dissertative.py`,
  `test_generation_adapter.py`, `test_runs.py`
- `backend/tests/integration/test_dissertative_pipeline.py`,
  `test_repositories.py`
- `docs/rag/NOTES.md`, `docs/rag/EVIDENCE.md`

## Conclusão

O bloqueador T13-R3-01 foi corrigido pela gramática realmente estrutural da
própria revisão (só whitespace num bloco nulo; a formatação fica com o
renderer) e verificado com testes adversariais de números/datas/quantidades
no domínio, no serviço e no adapter de geração, além de testes unitários
(570 passed) e de integração contra PostgreSQL real (218 passed, 1 skipped).
Não restam achados bloqueadores no escopo de T13; AC-09 fica coberto. A
revisão pode ser refeita.
