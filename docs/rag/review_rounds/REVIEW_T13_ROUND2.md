# Revisão independente — T13, rodada 2

Data: 2026-09-02
Referência: `REVIEW_T13.md` e `REVIEW_RESPONSE_T13.md`  
Commit revisado: `db844e1 Fix T13 review findings T13-01 through T13-03 (blocks binding approved)`

## Resultado

**Reprovado.** T13-01 e T13-02 foram corrigidos, mas T13-03 continua com um
bypass que permite prosa factual sem claim, evidência ou verificação. Isso é
bloqueador pelo checklist §12 e AC-09.

## Evidências executadas

| Comando | Resultado |
|---|---|
| `cd backend && uv run pytest tests/unit -q` | **558 passed, 3 skipped**; 6 warnings de depreciação de Docling. |
| `cd backend && DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration/test_dissertative_pipeline.py -q` | **7 passed** contra PostgreSQL real via Testcontainers/Podman. |
| `cd backend && uv run ruff check src tests` | **All checks passed**. |
| `cd backend && uv run ruff format --check src tests` | **116 files already formatted**. |
| `cd backend && uv run mypy src tests` | **Success: no issues found in 116 source files**. |
| Reprodutor in-memory abaixo | Aceita uma afirmação factual em bloco sem `claim_id`. |

Reprodutor executado:

```sh
cd backend && uv run python -c 'from uuid import uuid4; from rag.domain.answer import AnswerBlock, Claim, GeneratedAnswer; claim=Claim(id="c1", text="A fonte diz X.", evidence_ids=(uuid4(),)); blocks=(AnswerBlock(text=claim.text, claim_id="c1"), AnswerBlock(text=" Marte tem duas luas.", claim_id=None)); answer=GeneratedAnswer(answer_markdown="".join(b.text for b in blocks), blocks=blocks, claims=(claim,), limitations=(), abstained=False); print({"accepted": True, "unverified_block": answer.blocks[1].text, "unverified_claim_id": answer.blocks[1].claim_id})'
```

Saída:

```text
{'accepted': True, 'unverified_block': ' Marte tem duas luas.', 'unverified_claim_id': None}
```

## Revalidação dos achados anteriores

| Achado | Situação | Evidência |
|---|---|---|
| T13-01 | Corrigido | A abstenção rejeita Markdown, blocos e limitações; o serviço substitui a saída do gerador por uma abstenção canônica. Os novos testes de `TestGeneratedAnswer` e `TestGeneratorAbstention` passaram. |
| T13-02 | Corrigido | `assess_claims()` considera `contradiction=True` como não sustentado mesmo quando `supported=True`; o teste específico e o fluxo de serviço passaram. |
| T13-03 | **Não corrigido integralmente** | T13-R2-01 abaixo. |

## Achado

### T13-R2-01 — Crítico: `AnswerBlock` sem `claim_id` ainda transporta fatos não verificados

- Requisito: SPEC §9.4 requer verificar cada afirmação da resposta; AC-09
  proíbe afirmação factual sem evidência válida ou marcação explícita de
  inferência. O checklist §12 trata esse bypass como bloqueador.
- Evidência: `AnswerBlock.text` aceita qualquer texto e `claim_id` é opcional
  ([answer.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/domain/answer.py:39)). Em
  `GeneratedAnswer._blocks_cover_answer_markdown`, os blocos sem `claim_id`
  são simplesmente ignorados ([answer.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/domain/answer.py:106)).
  O reprodutor foi aceito com a frase factual “Marte tem duas luas.” sem claim.
  O serviço envia apenas `answer.claims` ao `VerifierProvider`
  ([dissertative.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/application/dissertative.py:211)); portanto esse bloco nunca é julgado.
- Impacto: o gerador pode colocar toda ou parte da resposta factual em blocos
  declarados como “prosa conectiva/estrutural”. A concatenação de blocos apenas
  prova que não há texto fora da lista; não prova que o texto dentro de um
  bloco nulo é não factual. A garantia declarada em `answer.py` de que nenhuma
  prosa factual atravessa sem claim é, assim, falsa.
- Correção necessária: impedir que texto semântico gerado tenha `claim_id`
  nulo. Uma solução fechada é permitir blocos nulos somente para tokens
  estruturais previamente definidos (por exemplo, whitespace e separadores
  Markdown controlados pelo servidor) e exigir que todo texto natural do
  modelo seja um bloco idêntico a uma claim. Alternativamente, normalizar e
  verificar também cada sentença dos blocos nulos antes da entrega. Adicionar
  teste adversarial equivalente ao reprodutor e um teste de serviço que prove
  que esse conteúdo não chega a uma resposta aceita.

## Critérios relevantes

| Critério | Situação |
|---|---|
| AC-09 | **Falha** — T13-R2-01 deixa afirmação factual fora da lista verificada. |
| AC-10 | Passa para o caso corrigido de abstenção. |
| AC-11 | Passa nos testes de T13, inclusive integração. |
| AC-14 | Passa nos testes unitários, de contrato e na integração direcionada. |
| AC-15 | Parcial — T18 ainda integra o `AnswerRun` completo. |

## Conclusão

As duas primeiras correções são coerentes e têm boa cobertura. A estratégia
aprovada de blocos/IDs só cumpre T13-03 se todo conteúdo natural do modelo for
associado a uma claim verificável. No estado atual, `claim_id=null` é uma rota
de evasão direta; T13 permanece reprovado até que ela seja fechada e testada.
