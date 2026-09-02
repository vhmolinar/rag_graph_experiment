# Resposta à revisão consolidada T13

Data: 2026-09-02
Referência: `docs/rag/review_rounds/REVIEW_T13_CONSOLIDATED.md`
Status: correções T13-FULL-01 a T13-FULL-03 implementadas e verificadas.
Resultado anterior: **reprovado** (T13-01 a T13-R3-01 corrigidos; T13-FULL-01
crítico, T13-FULL-02 alto, T13-FULL-03 alto).

Vias de correção aprovadas explicitamente pelo usuário nesta sessão:
- T13-FULL-01: limitações derivadas no serviço a partir de condições
  determinísticas;
- T13-FULL-03: atualização de `transformers` para resolver CVE-2026-9856.

---

## T13-FULL-01 (Crítico) — `limitations` ainda é prosa livre sem verificação

### Diagnóstico
`GeneratedAnswer.limitations` era `tuple[str, ...]` sem relação com claims ou
evidências; o verificador recebia só `answer.claims`. Uma afirmação factual em
`limitations` ("Marte tem duas luas.") era aceita e chegava a
`DissertativeAnswer` sem verificação (AC-09).

### Correção (vía aprovada: derivar no serviço)
1. **Domínio** (`domain/answer.py`): `limitations` foi REMOVIDO do contrato do
   gerador — `GeneratedAnswer` já não tem o campo; nenhuna prosa factual pode
   atravessar por esse canal. O campo extra num payload do modelo é ignorado
   (nunca entregue).
2. **Serviço** (`application/dissertative.py`): `DissertativeService._limitations`
   deriva as limitações DETERMINISTICAMENTE a partir de condições (hoje: AC-11
   comparativa com fonte única → `_SINGLE_SOURCE_LIMITATION`). São entregues em
   `DissertativeAnswer.limitations`, separadas da resposta do gerador.
3. **Contrato de geração**: `_GENERATION_OUTPUT_CONTRACT` declara que NON existe
   campo `limitations`.

### Evidências
- `tests/unit/test_answer.py::TestGeneratedAnswer::test_generated_answer_has_no_limitations_field`
  (domínio — o campo não existe);
- `tests/unit/test_generation_adapter.py::TestGenerate::test_model_limitations_are_dropped`
  (adapter — campo extra "limitations" com prosa factual é ignorado);
- `tests/unit/test_dissertative.py::TestComparativeLimitation::test_limitations_are_derived_not_generator_prose`
  (serviço — as entregues são EXACTAMENTE as derivadas, `_SINGLE_SOURCE_LIMITATION`);
- `test_factual_single_work_no_limitation` / `test_comparative_two_works_no_limitation`
  (condições determinísticas negativas).

Reproductor da revisão reexecutado:

```sh
cd backend && uv run python -c '... GeneratedAnswer(..., limitations=("Marte tem duas luas.",), ...); print(answer.limitations[0])'
```

Resultado:

```text
{'limitations_prose': '<campo inexistente — não entregue>'}
```

---

## T13-FULL-02 (Alto) — o verificador pode introduzir prosa factual em `detail`

### Diagnóstico
`ClaimVerdict.detail` era texto livre do verificador, copiado para
`Contradiction.detail` e devolvido no `VerificationResult` dentro de
`DissertativeAnswer` — conflita com SPEC §9.4 ("O verificador não pode
introduzir novas afirmações") e o checklist §12.

### Correção
1. **Domínio** (`domain/providers.py`): `ClaimVerdict.detail` foi REMOVIDO —
   a saída do verificador fica reduzida a IDs, flags e códigos (`claim_id`,
   `evidence_id`, `supported`, `contradiction`).
2. **Domínio** (`domain/verification.py`): `assess_claims` renderiza uma
   descrição FIXA e não factual (`_CONTRADICTION_DETAIL` =
   "A fonte contradice a afirmação.") na `Contradiction.detail` — nunca texto
   do verificador.
3. **Contrato de verificação**: `_VERIFICATION_OUTPUT_CONTRACT` declara que NON
   existe campo `detail`.

### Evidências
- `tests/unit/test_verifier_adapter.py::TestVerify::test_verifier_free_text_detail_is_ignored`
  — texto livre num campo extra "detail" do verificador não é exposto
  (`not hasattr(verdict, "detail")`);
- `tests/unit/test_verification.py::TestAssessClaims::test_contradiction_recorded_and_marks_unsupported`
  e `test_contradiction_marks_unsupported_even_when_supported_true` — a
  `Contradiction.detail` é `_CONTRADICTION_DETAIL` (texto fixo);
- Reproductor da revisão reexecutado: `Contradiction.detail` → `"A fonte
  contradice a afirmação."` (is_fixed=True), nunca "Marte tem duas luas.".

---

## T13-FULL-03 (Alto) — auditoria de dependências falha

### Diagnóstico
`make audit` falhava: `transformers 5.8.1` (transitiva via docling) com
`CVE-2026-9856`, versão-fixa 5.10.0.

### Correção (aprovada pelo usuário)
1. `transformers` fixada em `5.10.4` via `[tool.uv].constraint-dependencies`:
   `5.10.0` (a versão-fixa do CVE) está **yanked** em PyPI ("pushed from a
   week old main branch"); `5.10.4` é o patch mais recente não yanked que
   inclui a mesma correção.
2. `docling-core[chunking]` capa `transformers<5.9.0` em **darwin**; o
   ambiente-alvo da fase 1 é Linux (SPEC §1: "Docker Compose em uma VM
   Linux"), logo a resolução foi limitada com
   `[tool.uv].environments = ["sys_platform == 'linux'"]` (documentado em
   NOTES.md §19).
3. Lockfile regenerado (`uv lock`): `transformers 5.8.1 → 5.10.4`; pacotes de
   outras plataformas (colorama, pywin32, tzdata) saíron por desenho da
   limitação.

### Evidências
- `make audit` → `No known vulnerabilities found` (backend); npm
  `found 0 vulnerabilities`;
- `make lock` → passou;
- `make security-scan` → nenhum IOC bloqueado;
- `uv run pytest tests/unit -q` → 573 passed, 3 skipped (com transformers
  5.10.4 instalado);
- `uv run pytest tests/integration -q` → 218 passed, 1 skipped (PostgreSQL
  real) com a nova resolução.

---

## Evidências executadas (2026-09-02, Linux; PostgreSQL real via testcontainers sobre podman)

Ambiente: `DOCKER_HOST=unix:///run/user/1000/podman/podman.sock`,
`TESTCONTAINERS_RYUK_DISABLED=true`.

| Comando | Resultado |
|---|---|
| `make lock` | OK |
| `uv run ruff check src tests` | OK — All checks passed |
| `uv run ruff format --check src tests` | OK — 116 arquivos |
| `uv run mypy src tests` | OK — 116 arquivos, strict, 0 issues |
| `uv run pytest tests/unit -q` | OK — 573 passed, 3 skipped (e2e opcionais) |
| `uv run pytest tests/integration -q` | OK — 218 passed, 1 skipped (PostgreSQL real) |
| `uv run pytest tests/contract -q` | OK — 26 passed |
| `make security-scan` | OK — nenhum IOC bloqueado |
| `make audit` | OK — No known vulnerabilities found (backend); npm 0 vulnerabilidades |
| Reproductores T13-FULL-01/T13-FULL-02 | cerrados (ver acima) |

Arquivos alterados:
- `backend/src/rag/domain/answer.py` (T13-FULL-01)
- `backend/src/rag/domain/providers.py` (T13-FULL-02)
- `backend/src/rag/domain/verification.py` (T13-FULL-02)
- `backend/src/rag/application/dissertative.py` (T13-FULL-01, T13-FULL-02)
- `backend/pyproject.toml`, `backend/uv.lock` (T13-FULL-03)
- `backend/tests/unit/test_answer.py`, `test_dissertative.py`,
  `test_generation_adapter.py`, `test_verification.py`,
  `test_verifier_adapter.py`, `test_runs.py`, `test_model_doubles.py`
- `backend/tests/integration/test_dissertative_pipeline.py`,
  `test_repositories.py`
- `docs/rag/NOTES.md`, `docs/rag/EVIDENCE.md`

## Conclusão

Os dois canais textuais não fundamentados foram fechados em conjunto:
`limitations` saiu do contrato do gerador (derivadas no serviço, T13-FULL-01)
e a saída do verificador fica reduzida a IDs/flags/códigos com descrição fixa
(T13-FULL-02). A auditoria de dependências passou após a atualização de
`transformers` (T13-FULL-03, aprovada). Todos os gates do projeto estão verdes
(unit 573, integração 218, contrato 26, lint, format, mypy, audit,
security-scan, lock). Não restam achados bloqueadores no escopo de T13; AC-09,
AC-10, AC-11, AC-14 e AC-15 ficam cobertos. A revisão pode ser refeita.
