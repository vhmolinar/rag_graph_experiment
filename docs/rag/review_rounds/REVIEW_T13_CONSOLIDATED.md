# Revisão independente consolidada — T13

Data: 2026-09-02  
Referências: `SPECIFICATION.md` §9.3–§9.4, AC-09 a AC-11, `REVIEW_CHECKLIST.md`
§12 e respostas/correções das rodadas 1 a 3.

## Resultado

**Reprovado.** As três falhas das rodadas anteriores foram corrigidas, mas a
implementação ainda deixa dois canais textuais não fundamentados atravessarem a
resposta/resultado de verificação. Há também uma dependência com CVE conhecido,
que impede a evidência de segurança de passar.

## Estado dos achados anteriores

| Item | Estado | Evidência |
|---|---|---|
| T13-01 — abstenção com prosa arbitrária | Corrigido | `GeneratedAnswer` recusa texto, blocos e limitações em abstenção; o serviço substitui a resposta do modelo pela forma canônica. |
| T13-02 — `supported=true` + `contradiction=true` preservava claim factual | Corrigido | `assess_claims` trata qualquer contradição como não sustentada; há testes dedicados. |
| T13-03 / R2 / R3 — Markdown fora de claims | Corrigido | `answer_markdown` deve ser a concatenação dos blocos; bloco sem `claim_id` aceita somente whitespace. A tentativa com texto numérico não passa mais. |

## Achados bloqueadores

### T13-FULL-01 — Crítico: `limitations` ainda é prosa livre sem verificação

`GeneratedAnswer.limitations` é `tuple[str, ...]` sem relação com `claims` ou
evidências (`backend/src/rag/domain/answer.py:79`). A verificação envia somente
`answer.claims` ao `VerifierProvider`
(`backend/src/rag/application/dissertative.py:215`). Logo, uma afirmação factual
em `limitations` é aceita e pode chegar no `DissertativeAnswer` sem ser
fundamentada nem marcada como inferência. Isso viola AC-09 e é um bypass direto
do verificador.

Reprodutor executado:

```sh
cd backend && uv run python -c 'from uuid import uuid4; from rag.domain.answer import AnswerBlock, Claim, GeneratedAnswer; claim=Claim(id="c1", text="A fonte diz X.", evidence_ids=(uuid4(),)); answer=GeneratedAnswer(answer_markdown=claim.text, blocks=(AnswerBlock(text=claim.text, claim_id="c1"),), claims=(claim,), limitations=("Marte tem duas luas.",), abstained=False); print(answer.limitations[0])'
```

Saída: `Marte tem duas luas.`

Correção necessária: não aceite limitações livres do gerador. Derive as
limitações no serviço a partir de códigos/condições determinísticos, ou modele
cada uma como claim (inclusive com `inference`) e submeta-a ao mesmo fluxo de
verificação. Cubra domínio, adapter e serviço com uma limitação factual
maliciosa.

### T13-FULL-02 — Alto: o verificador pode introduzir prosa factual em `detail`

O contrato do `VerifierProvider` permite `ClaimVerdict.detail: str | None`
(`backend/src/rag/domain/providers.py:218-227`). Quando há contradição,
`assess_claims` copia esse texto para `Contradiction.detail`
(`backend/src/rag/domain/verification.py:176-179`), e o serviço retorna o
`VerificationResult` dentro de `DissertativeAnswer`
(`backend/src/rag/application/dissertative.py:458-466`). Assim, a prosa do
verificador chega ao resultado sem contrato de evidência. Isso conflita
diretamente com SPEC §9.4: “O verificador não pode introduzir novas
afirmações”, e com o checklist §12.

Reprodutor executado:

```sh
cd backend && uv run python -c 'from uuid import uuid4; from rag.domain.answer import Claim; from rag.domain.providers import ClaimVerdict; from rag.domain.verification import assess_claims; evidence_id=uuid4(); claim=Claim(id="c1", text="A fonte diz X.", evidence_ids=(evidence_id,)); result=assess_claims((claim,), (ClaimVerdict(claim_id="c1", evidence_id=evidence_id, supported=False, contradiction=True, detail="Marte tem duas luas."),)); print(result[0].contradictions[0].detail)'
```

Saída: `Marte tem duas luas.`

Correção necessária: reduza a saída do verificador a IDs, flags e códigos
enumerados; o serviço pode renderizar descrições fixas e não factuais. Se um
detalhe textual for indispensável, ele deve ser uma referência a evidência
existente, não texto livre. Teste explicitamente que texto arbitrário do
verificador não é exposto nem persistido.

### T13-FULL-03 — Alto: auditoria de dependências falha

`make audit` falha com uma vulnerabilidade conhecida:

```text
transformers 5.8.1   CVE-2026-9856   Fix Versions: 5.10.0
```

O lockfile está íntegro e a varredura dos IOCs bloqueados passa, mas a evidência
de segurança definida pelo próprio projeto não está verde. Atualizar a versão é
uma alteração de dependência e exige a aprovação explícita prevista em
`AGENTS.md`; esta revisão não a fez.

## Evidências executadas

| Comando | Resultado |
|---|---|
| `make lock` | Passou. |
| `cd backend && uv run ruff check src tests` | Passou. |
| `cd backend && uv run ruff format --check src tests` | Passou: 116 arquivos. |
| `cd backend && uv run mypy src tests` | Passou: 116 arquivos. |
| `cd backend && uv run pytest tests/unit -q` | **570 passed, 3 skipped**. |
| `cd backend && DOCKER_HOST=unix:///run/user/1000/podman/podman.sock TESTCONTAINERS_RYUK_DISABLED=true uv run pytest tests/integration -q` | **218 passed, 1 skipped** contra PostgreSQL real. |
| `cd backend && uv run pytest tests/contract -q` | **26 passed**. |
| `make security-scan` | Passou: nenhum IOC bloqueado. |
| `make audit` | **Falhou**: CVE-2026-9856 em `transformers 5.8.1`. |
| `make lint`, `make format-check`, `make typecheck` | Backend passou; etapa frontend não iniciou, pois `frontend/node_modules` não existe (`eslint`, `prettier`, `tsc` ausentes). |

Os testes produziram somente avisos de depreciação de dependências Docling; não
houve falhas de teste. Eles não detectam os dois bypasses porque não exercitam
texto livre em `limitations` nem em `ClaimVerdict.detail`.

## Conclusão e próxima entrega esperada

T13 não pode ser aprovado enquanto qualquer texto gerado por modelo puder sair
de `answer_markdown`/claims e das estruturas de verificação sem um vínculo
verificável. A próxima resposta de implementação deve corrigir **os dois canais
em conjunto**, acrescentar os testes negativos indicados e informar se a
atualização de `transformers` foi aprovada. Isso evita novas rodadas separadas
para a mesma classe de falha.
