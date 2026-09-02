# Revisão independente — T13, rodada 4

Data: 2026-09-02
Referência: `REVIEW_T13_ROUND3.md` e `REVIEW_RESPONSE_T13_ROUND3.md`

## Resultado

**Reprovado.** A gramática de whitespace elimina o bypass nos blocos de
Markdown, mas `GeneratedAnswer.limitations` continua uma lista de texto livre
produzida pelo modelo. Ela é entregue ao cliente sem ligação a `claims`,
evidências ou verificação, contrariando AC-09.

## Evidências executadas

| Comando | Resultado |
|---|---|
| Reprodutor in-memory abaixo | Aceita uma limitação factual sem claim. |
| `cd backend && uv run pytest tests/unit/test_answer.py tests/unit/test_dissertative.py tests/unit/test_generation_adapter.py tests/unit/test_verification.py tests/unit/test_verifier_adapter.py -q` | **108 passed**. |
| `cd backend && DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration/test_dissertative_pipeline.py -q` | **7 passed** contra PostgreSQL real. |
| `cd backend && uv run ruff check src tests` | **All checks passed**. |
| `cd backend && uv run ruff format --check src tests` | **116 files already formatted**. |
| `cd backend && uv run mypy src tests` | **Success: no issues found in 116 source files**. |

Reprodutor executado:

```sh
cd backend && uv run python -c 'from uuid import uuid4; from rag.domain.answer import AnswerBlock, Claim, GeneratedAnswer; claim=Claim(id="c1", text="A fonte diz X.", evidence_ids=(uuid4(),)); answer=GeneratedAnswer(answer_markdown=claim.text, blocks=(AnswerBlock(text=claim.text, claim_id="c1"),), claims=(claim,), limitations=("Marte tem duas luas.",), abstained=False); print({"accepted": True, "unverified_limitation": answer.limitations[0]})'
```

Saída:

```text
{'accepted': True, 'unverified_limitation': 'Marte tem duas luas.'}
```

## Achado

### T13-R4-01 — Crítico: `limitations` permite conteúdo factual não verificado

- Requisito: SPEC §9.3 inclui `limitations` na saída interna entregue pelo
  modo dissertativo; SPEC §9.4 e AC-09 exigem que toda afirmação factual da
  resposta seja fundamentada ou marcada como inferência.
- Evidência: `limitations` é `tuple[str, ...]`, sem vínculo a claims
  ([answer.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/domain/answer.py:63)).
  A validação de blocos não a examina, e o serviço fornece ao verificador
  somente `answer.claims` ([dissertative.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/application/dissertative.py:211)).
  O reprodutor constrói uma resposta válida com a afirmação “Marte tem duas
  luas.” em `limitations`, sem claim ou evidência.
- Impacto: o modelo pode apresentar conhecimento externo como “limitação”,
  contornando os controles introduzidos para Markdown. O campo é parte de
  `GeneratedAnswer` e, portanto, pode alcançar o cliente junto da resposta.
- Correção necessária: restrinja `limitations` a uma enumeração/estrutura
  derivada deterministamente pelo serviço (por exemplo, fonte única e ausência
  de suporte), ou represente cada limitação do gerador como claim verificada ou
  inferência explicitamente marcada. Rejeite strings livres do gerador e
  adicione testes no domínio, adapter e serviço para uma limitação factual
  maliciosa.

## Conclusão

Os blocos agora estão coerentemente fechados, mas a garantia precisa abranger
todos os campos visíveis da resposta, não só `answer_markdown`. T13 permanece
reprovado até que `limitations` deixe de ser um canal de prosa não verificada.
