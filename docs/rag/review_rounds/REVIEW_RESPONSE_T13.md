# Resposta à revisão T13

Data: 2026-09-02
Referência: `docs/rag/review_rounds/REVIEW_T13.md`
Status: correções T13-01 a T13-03 implementadas e verificadas.
Resultado anterior: **reprovado** (T13-01 crítico; T13-02 alto; T13-03 alto).

A via de correção de T13-03 (blocos/IDs de claim vs. extração determinística)
foi aprovada explicitamente pelo usuário nesta sessão: **blocos com IDs de
claim** (recomendada pela revisão como primeira opção).

---

## T13-01 (Crítico) — uma "abstenção" do gerador pode entregar prosa factual arbitrária

### Diagnóstico
`GeneratedAnswer` aceitava `abstained=True`, `claims=()` e qualquer
`answer_markdown`; `DissertativeService._verify()` retornava sucesso sem
chamar o verificador e `answer()` devolvia o objeto original. O reprodutor da
revisão confirmava que `"Fato inventado apresentado ao usuário."` era válido.

### Correção
1. **Domínio** (`domain/answer.py`): a abstenção agora exige `answer_markdown`
   vazio, sem blocos e sem limitações — uma "abstenção" com Markdown não vazio
   é rejeitada por construção (`ValidationError`; no adapter, `ModelResponseError`).
2. **Serviço** (`application/dissertative.py`): quando o gerador declara
   abstenção, o serviço substitui a saída pela forma canônica
   (`_abstained_answer(_ABSTENTION_REASON)`) — nem o `abstention_reason` do
   gerador atravessa o caminho de abstenção.

### Evidências
- `tests/unit/test_answer.py::TestGeneratedAnswer::test_abstained_answer_cannot_have_text`
- `tests/unit/test_answer.py::TestGeneratedAnswer::test_abstained_answer_cannot_have_limitations`
- `tests/unit/test_answer.py::TestGeneratedAnswer::test_abstained_answer_cannot_have_blocks`
- `tests/unit/test_dissertative.py::TestGeneratorAbstention::test_generator_abstention_is_replaced_with_canonical`
- Reproductor da revisão reexecutado: `GeneratedAnswer(answer_markdown="Fato inventado...", abstained=True)` agora rejeita com `Value error, resposta abstida não pode conter texto (AC-10)`.

---

## T13-02 (Alto) — um veredicto contraditório e `supported=true` pode manter a afirmação factual

### Diagnóstico
`assess_claims()` só alterava `supported` quando `verdict.supported` era falso;
para `supported=True, contradiction=True` o reprodutor da revisão producia
`contradiction_is_supported=True`, e `_finalize()` não o marcava porque não
integrava `unsupported_claim_ids`.

### Correção
`assess_claims()` (`domain/verification.py`) tornou o par não sustentado
sempre que o veredicto tiver `contradiction=true`, INDEPENDENTEMENTE de
`supported`. Assim a combinação `supported=true, contradiction=true`
(permitida pelo schema) entra em `unsupported_claim_ids` e é marcada como
inferência no caminho de correção — nunca é liberada como factual (AC-09).

### Evidências
- `tests/unit/test_verification.py::TestAssessClaims::test_contradiction_marks_unsupported_even_when_supported_true`
- `tests/unit/test_dissertative.py::TestUnsupportedClaims::test_contradiction_with_supported_true_marks_inference`
  (serviço: `supported_claims == 0`, `unsupported_claim_ids == ("c1",)`, `inference is True`)
- Reproductor da revisão reexecutado: `contradiction_is_supported -> False`, `contradições -> 1`.

---

## T13-03 (Alto) — o Markdown entregue não é vinculado às claims verificadas

### Diagnóstico
`GeneratedAnswer` não exigia que as claims cubressem `answer_markdown`; o
serviço enviaba só `answer.claims` ao verificador e devolvia o Markdown
original intacto, inclusive no caminho de correção. Uma saída podia ter uma
claim sustentada e adicionar no Markdown uma segunda afirmação factual
inventada; apenas a primeira seria julgada.

### Correção (via aprovada pelo usuário: blocos com IDs de claim)
`GeneratedAnswer` ganhou `blocks: tuple[AnswerBlock]` (`AnswerBlock` = `text`
+ `claim_id` opcional) com invariantes validados no domínio:

1. `answer_markdown == "".join(block.text)` — nenhuna prosa factual pode
   existir fora dos blocos (falha fechada);
2. cada bloco de afirmação (`claim_id` informado) deve corresponder verbatim à
   uma `Claim` existente;
3. cada `Claim` deve aparecer como bloco (nenhuna afirmação verificada fica
   invisível no texto entregue).

O contrato de saída do prompt de geração
(`_GENERATION_OUTPUT_CONTRACT` em `application/dissertative.py`) foi
atualizado para instruir o modelo a devolver `blocks`. Sem migration: a
resposta é conteúdo JSONB de `answer_runs.response`, não coluna relacional.

### Evidências
- `tests/unit/test_answer.py::TestGeneratedAnswer`:
  - `test_answer_requires_blocks_covering_markdown` (blocos requeridos);
  - `test_markdown_must_equal_concatenation_of_blocks` (afirmação factual
    extra não listada no Markdown → rejeitada);
  - `test_block_referencing_unknown_claim_is_rejected`;
  - `test_claim_block_must_match_claim_text`;
  - `test_every_claim_must_appear_as_block`.
- `tests/unit/test_dissertative.py::TestAnswerMarkdownBinding::test_corrected_answer_keeps_markdown_bound_to_claims`
  (o caminho de correção preserva a ligação Markdown↔claims).

---

## Risco residual da revisão — integração PostgreSQL não executada

A revisão não pôde executar `uv run pytest tests/integration/test_dissertative_pipeline.py`
por falta de daemon Docker. Nesta sessão o ambiente dispõe de Podman
(socket em `/run/user/1000/podman/podman.sock`), e a integração foi executada
contra PostgreSQL real via testcontainers, sem converter os erros de setup em
skips:

| Comando | Resultado |
|---|---|
| `uv run pytest tests/integration/test_dissertative_pipeline.py -q` | OK — 7 passed (PostgreSQL real) |
| `uv run pytest tests/integration -q` | OK — 218 passed, 1 skipped (PostgreSQL real) |

---

## Evidências executadas (2026-09-02, Linux; PostgreSQL real via testcontainers sobre podman)

Ambiente: `DOCKER_HOST=unix:///run/user/1000/podman/podman.sock`,
`TESTCONTAINERS_RYUK_DISABLED=true`.

| Comando | Resultado |
|---|---|
| `uv run ruff check src tests` | OK — All checks passed |
| `uv run ruff format --check src tests` | OK — 116 arquivos |
| `uv run mypy src tests` | OK — 116 arquivos, strict, 0 issues |
| `uv run pytest tests/unit -q` | OK — 558 passed, 3 skipped (e2e opcionais) |
| `uv run pytest tests/integration/test_dissertative_pipeline.py -q` | OK — 7 passed (PostgreSQL real) |
| `uv run pytest tests/integration -q` | OK — 218 passed, 1 skipped (PostgreSQL real) |
| Reproductor da revisão (T13-01/T13-02) | ambos comportamentos corrigidos (ver acima) |

Arquivos alterados:
- `backend/src/rag/domain/answer.py` (T13-01, T13-03)
- `backend/src/rag/domain/verification.py` (T13-02)
- `backend/src/rag/application/dissertative.py` (T13-01, T13-03)
- `backend/tests/fixtures/model_doubles.py` (blocos no default)
- `backend/tests/unit/test_answer.py`, `test_verification.py`,
  `test_dissertative.py`, `test_providers.py`, `test_runs.py`,
  `test_serialization.py`, `test_generation_adapter.py`
- `backend/tests/integration/test_dissertative_pipeline.py`,
  `test_repositories.py`
- `docs/rag/NOTES.md`, `docs/rag/EVIDENCE.md`

## Conclusão

Os bloqueadores T13-01, T13-02 e T13-03 de REVIEW_T13.md foram corrigidos e
verificados com testes unitários (558 passed) e de integração contra
PostgreSQL real (218 passed, 1 skipped), executados de facto no fluxo real de
recuperação + montagem + geração + verificação. A matriz `AC-01` a `AC-20` foi
atualizada em `docs/rag/EVIDENCE.md` (AC-09, AC-10; AC-11, AC-14, AC-15
reconfirmados). A revisão pode ser refeita.
