# Revisão independente — T13

Data: 2026-09-01
Resultado: **reprovado**

## Escopo revisado

- Commit/branch: `8686052c288f2f7025f6272b239648ff1ffe4c4e` / `T13`.
- Escopo: T13 — geração dissertativa e verificação; interfaces diretas em
  `answer.py`, `verification.py`, `providers.py` e adapter HTTP do verificador.
- Ambiente: Linux; uv 0.12.7; Python 3.12.14; pytest 9.1.1; ruff 0.16.5;
  mypy 2.3.1. O daemon Docker não está disponível neste ambiente.
- Estado do worktree antes da criação deste parecer: limpo.

## Evidências executadas

| Comando | Resultado |
|---|---|
| `cd backend && uv run pytest tests/unit/test_dissertative.py tests/unit/test_verification.py tests/unit/test_verifier_adapter.py -q` | **42 passed** em 0,60 s. |
| `cd backend && uv run ruff check src tests` | **All checks passed**. |
| `cd backend && uv run ruff format --check src tests` | **113 files already formatted**. |
| `cd backend && uv run mypy src tests` | **Success: no issues found in 113 source files**. |
| `cd backend && uv run pytest tests/integration/test_dissertative_pipeline.py -q` | **Não executável**: 7 erros no setup, pois Testcontainers não encontrou `/var/run/docker.sock` (`DockerException`). Não é falha atribuída ao código, mas deixa a evidência de integração indisponível nesta revisão. |
| Reprodutor in-memory abaixo | Confirma os dois comportamentos descritos nos achados T13-01 e T13-02. |

Reprodutor executado:

```sh
cd backend && uv run python -c 'from uuid import uuid4; from rag.domain.answer import Claim, GeneratedAnswer; from rag.domain.providers import ClaimVerdict; from rag.domain.verification import assess_claims; evidence_id=uuid4(); abstained=GeneratedAnswer(answer_markdown="Fato inventado apresentado ao usuário.", claims=(), limitations=(), abstained=True, abstention_reason="Sem suporte."); assessment=assess_claims((Claim(id="c1", text="A fonte diz X.", evidence_ids=(evidence_id,)),), (ClaimVerdict(claim_id="c1", evidence_id=evidence_id, supported=True, contradiction=True),)); print({"abstention_markdown": abstained.answer_markdown, "contradiction_is_supported": assessment[0].supported, "contradiction_count": len(assessment[0].contradictions)})'
```

Saída:

```text
{'abstention_markdown': 'Fato inventado apresentado ao usuário.', 'contradiction_is_supported': True, 'contradiction_count': 1}
```

## Critérios

| Critério | Situação nesta revisão | Evidência |
|---|---|---|
| AC-01 a AC-08 | Fora do escopo de T13 | Não reavaliados. |
| AC-09 | **Falha** | T13-01 e T13-02 permitem texto factual não ligado a uma claim verificada e uma claim contraditória continuar factual. |
| AC-10 | **Falha** | T13-01 devolve literalmente `answer_markdown` arbitrário quando o gerador declara abstenção. |
| AC-11 | Passa no caminho unitário | `TestComparativeLimitation` passou; não houve confirmação de integração por indisponibilidade do Docker. |
| AC-12 e AC-13 | Fora do escopo de T13 | Não reavaliados. |
| AC-14 | Passa nos testes unitários/contrato | Timeout do verificador é encapsulado como `VerificationError`; 42 testes unitários selecionados passaram. Evidência de integração indisponível. |
| AC-15 | Evidência parcial | T13 registra versões próprias; o `AnswerRun` completo é T18 e não foi reavaliado. |
| AC-16 a AC-20 | Fora do escopo de T13 | Não reavaliados. |

## Achados

### T13-01 — Crítico: uma “abstenção” do gerador pode entregar prosa factual arbitrária sem verificação

- Requisito: SPEC §9.4 exige que toda resposta dissertativa passe pela segunda
  etapa; AC-09 proíbe afirmação factual sem evidência válida/inferência marcada;
  AC-10 exige abstenção quando não há suporte.
- Evidência: `GeneratedAnswer` aceita `abstained=True`, `claims=()` e qualquer
  `answer_markdown` ([answer.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/domain/answer.py:38)).
  `DissertativeService._verify()` retorna sucesso sem chamar o verificador para
  essa forma ([dissertative.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/application/dissertative.py:186));
  em seguida `answer()` devolve o objeto original ([dissertative.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/application/dissertative.py:163)). O reprodutor acima mostra que
  `"Fato inventado apresentado ao usuário."` é válido.
- Impacto: um modelo pode marcar uma resposta como abstida, mas preencher o
  Markdown com uma resposta não fundamentada. O serviço a entrega como
  `ACCEPTED`, sem verificação. O teste atual fixa somente Markdown vazio e
  afirma explicitamente que o verificador não é chamado
  ([test_dissertative.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/tests/unit/test_dissertative.py:180)); portanto não cobre o caso adversarial.
- Correção necessária: tornar `answer_markdown` vazio/estritamente controlado
  quando `abstained=True` e o serviço substituir a saída do gerador por uma
  abstenção canônica; alternativamente verificar essa forma antes de entregá-la.
  Adicionar testes negativos para Markdown não vazio, limitações não permitidas
  e para garantir que nenhuma prosa do gerador atravessa o caminho de abstenção.

### T13-02 — Alto: um veredicto contraditório e “supported=true” pode manter a afirmação como factual

- Requisito: SPEC §9.4 requer detectar contradições e remover, corrigir ou
  marcar a inferência; AC-09 exige marcação explícita quando não há suporte
  válido.
- Evidência: `assess_claims()` documenta que suporte requer ausência de
  contradição, mas só altera `supported` quando `verdict.supported` é falso
  ([verification.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/domain/verification.py:150)).
  Para `supported=True, contradiction=True`, o reprodutor produz
  `contradiction_is_supported=True`. No fim das tentativas,
  `_finalize()` marca apenas `unsupported_claim_ids`
  ([dissertative.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/application/dissertative.py:261)); como a claim contraditória não integra essa lista, ela continua com
  `inference=False` se o limiar de cobertura for alcançado.
- Impacto: uma resposta que contradiz sua fonte pode ser liberada como factual.
  O teste de contradição existente usa o double que também devolve
  `supported=False`; não cobre a combinação que o schema público permite
  ([test_dissertative.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/tests/unit/test_dissertative.py:331)).
- Correção necessária: uma contradição deve sempre tornar o par/claim não
  sustentado, independentemente do valor de `supported`; rejeitar ou normalizar
  veredictos logicamente inconsistentes e incluir testes unitário e de serviço
  para `supported=True, contradiction=True`.

### T13-03 — Alto: o Markdown entregue não é vinculado às `claims` verificadas

- Requisito: SPEC §9.3 define `answer_markdown` como a resposta e §9.4 manda
  dividir e normalizar as afirmações da resposta, verificando cada uma. AC-09
  abrange toda afirmação factual da resposta, não apenas uma lista auxiliar.
- Evidência: `GeneratedAnswer` não exige que as claims cubram
  `answer_markdown` ([answer.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/domain/answer.py:38)).
  O serviço envia somente `answer.claims` ao verificador
  ([dissertative.py](/data/dev/src/ai-stuff/rag_graph_experiment_T13/backend/src/rag/application/dissertative.py:199)) e devolve o Markdown original intacto, inclusive no caminho de correção.
  Assim, uma saída pode ter uma claim sustentada e adicionar no Markdown uma
  segunda afirmação factual inventada; apenas a primeira será julgada.
- Impacto: o mecanismo não implementa a exigência de verificar cada afirmação
  que o usuário lê. É um bypass direto do objetivo de evidência do modo
  dissertativo.
- Correção necessária: definir uma representação de resposta que associe o
  texto visível às claims (por exemplo, blocos/IDs de claim) e validar cobertura
  completa antes da entrega, ou extrair deterministicamente as afirmações do
  Markdown e falhar fechado quando não houver mapeamento. Incluir teste com
  Markdown contendo uma afirmação extra não listada.

## Riscos residuais

- A integração PostgreSQL real de T13 não foi executada nesta máquina por falta
  do daemon Docker. Ela deve ser executada uma vez que o serviço esteja
  disponível, sem converter os sete erros atuais em skips.
- O parecer não reavalia tarefas posteriores nem os critérios fora do escopo
  T13.

## Conclusão

T13 não satisfaz os bloqueadores de fundamentação do modo dissertativo. Os
testes existentes demonstram caminhos esperados, mas não impedem a entrega de
prosa não verificada nem a aceitação de uma claim contraditória. Corrigir
T13-01, T13-02 e T13-03, adicionar os testes negativos indicados e repetir os
comandos acima (incluindo a suíte de integração com Docker) antes de nova
revisão.
