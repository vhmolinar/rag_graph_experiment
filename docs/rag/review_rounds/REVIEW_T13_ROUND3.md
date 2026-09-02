# Revisão independente — T13, rodada 3

Data: 2026-09-02
Referência: `REVIEW_T13_ROUND2.md` e `REVIEW_RESPONSE_T13_ROUND2.md`

## Resultado

**Reprovado.** A correção de T13-R2-01 fecha texto alfabético sem claim, mas
mantém um bypass para conteúdo factual numérico. Como respostas a perguntas de
data, quantidade, capítulo, página ou identificação podem ser somente números,
o bypass viola AC-09.

## Evidências executadas

| Comando | Resultado |
|---|---|
| Reprodutor in-memory abaixo | Aceita `2024` como bloco sem claim, junto de uma claim verificada. |
| `cd backend && uv run pytest tests/unit/test_answer.py tests/unit/test_dissertative.py tests/unit/test_generation_adapter.py tests/unit/test_verification.py tests/unit/test_verifier_adapter.py -q` | **101 passed**. |
| `cd backend && DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration/test_dissertative_pipeline.py -q` | **7 passed** contra PostgreSQL real. |
| `cd backend && uv run ruff check src tests` | **All checks passed**. |
| `cd backend && uv run ruff format --check src tests` | **116 files already formatted**. |
| `cd backend && uv run mypy src tests` | **Success: no issues found in 116 source files**. |

Reprodutor executado:

```sh
cd backend && uv run python -c 'from uuid import uuid4; from rag.domain.answer import AnswerBlock, Claim, GeneratedAnswer; claim=Claim(id="c1", text="A fonte contém uma data.", evidence_ids=(uuid4(),)); blocks=(AnswerBlock(text=claim.text, claim_id="c1"), AnswerBlock(text=" 2024", claim_id=None)); answer=GeneratedAnswer(answer_markdown="".join(b.text for b in blocks), blocks=blocks, claims=(claim,), limitations=(), abstained=False); print({"accepted": True, "unverified_block": answer.blocks[1].text, "unverified_claim_id": answer.blocks[1].claim_id})'
```

Saída:

```text
{'accepted': True, 'unverified_block': ' 2024', 'unverified_claim_id': None}
```

## Achado

### T13-R3-01 — Crítico: a categoria “estrutural” permite afirmações numéricas não verificadas

- Requisito: AC-09 e SPEC §9.4 exigem que cada afirmação factual tenha
  evidência válida ou marcação explícita de inferência.
- Evidência: `_is_structural_text()` define bloco nulo como qualquer texto sem
  caracteres alfabéticos ([answer.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/domain/answer.py:26)). Logo,
  `" 2024"` é aceito em `AnswerBlock(claim_id=None)` e ignorado pela validação
  de ligação ([answer.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/domain/answer.py:125)).
  O reprodutor acima cria uma resposta não abstida válida. O verificador recebe
  somente `answer.claims` ([dissertative.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/application/dissertative.py:211)); esse número não é julgado.
- Impacto: para “Em que ano ocorreu X?” ou “Quantos capítulos há?”, um modelo
  pode anexar uma resposta numérica não sustentada a uma claim irrelevante que
  seja sustentada. A defesa em profundidade repete o mesmo validator e também
  aceita esse conteúdo.
- Correção necessária: não use “ausência de letras” como classificação de
  estrutura. Restrinja blocos sem claim a uma gramática explícita de tokens
  renderizados pelo servidor (por exemplo, whitespace e marcadores de citação
  com IDs autorizados), ou proíba inteiramente esses blocos na saída do modelo
  e deixe o renderer inserir formatação. Qualquer dígito, URL, emoji ou símbolo
  semântico fornecido pelo modelo deve pertencer a uma claim verificada. Adicione
  testes de números/datas/quantidades sem `claim_id`, inclusive no serviço.

## Conclusão

O conjunto de testes passa, mas não exerce a fronteira semântica criada pela
nova regra. T13 continua reprovado até que blocos sem claim sejam limitados a
uma gramática realmente estrutural e o reprodutor numérico seja rejeitado.
