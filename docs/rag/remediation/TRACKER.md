# Remediação R01–R22 — Tarefas e rastreamento

Este documento é o guia para agentes executarem a remediação das falhas da
reprovação consolidada (`REVIEW_T01_T14_CONSOLIDATED.md`).

## Instruções

1. **Escolha uma tarefa**: pegue a primeira disponível da tabela abaixo
   (prioridade `P0` > `P1` > `P2` > `P3`). Respeite as dependências
   (o estado da tarefa deve ser `concluída` antes que outra possa depender dela).
2. **Crie um branch empilhado (stacked)**: o branch da tarefa vem do branch
   da tarefa anterior no fluxo sequencial (ou de `main` se não houver).
   Ex.: `remediation/R01`, `remediation/R02` baseada em `remediation/R01`.
3. **Use worktree separado** para trabalhar paralelamente se necessário.
4. **Ao concluir**, abra um PR contra `main` referenciando a tarefa e marque
   o estado como `concluída`. Inclua testes positivos, negativos e de falha.

Regras gerais:

- Não altere requisitos, critérios `AC-*` ou `REVIEW_CHECKLIST.md` para parecer conforme.
- Não adicione dependências sem aprovação; use registros oficiais e versões pinadas.
- Backend Python tipado; domínio independente de FastAPI/ORM/SDKs.
- SQL parametrizado; segredos só por ambiente/secret files.
- Falhas fechadas; nunca gere resposta sem evidências como fallback.
- Execute lint, type checking e testes; registre comandos e resultados.
- Atualize `EVIDENCE.md` apenas para os ACs que a tarefa afeta de fato.

## Tarefas

| ID | Descrição | Onda | Depende de | ACs | Prioridade | Estado | Notas |
|---|---|---|---|---|---|---|---|
| R01 | Fidelidade literal de EPUB | 1 | — | AC-03, AC-08 | P0 | concluída | |
| R02 | `literal` realmente literal | 1 | — | AC-05, AC-06, AC-11, AC-15 | P0 | executando | |
| R03 | Execução da estratégia `expanded` | 2 | R02 | AC-05, AC-11, AC-15 | P0 | pendente | |
| R04 | Recuperação hierárquica | 2 | R03 | AC-11, AC-12 | P0 | pendente | |
| R05 | Limitações dissertativas na API | 1 | — | AC-11, AC-15 | P0 | executando | |
| R06 | Fechar bypass de inferências | 1 | contrato inferência | AC-09, AC-10, AC-14 | P0 | pendente | |
| R07 | Cancelamento completo | 3 | R05 | AC-18 | P1 | pendente | |
| R08 | Filtro efetivo único | 1 | — | AC-07 | P1 | pendente | |
| R09 | Rastreabilidade de versões | 3 | R03, R04, R05 | AC-15 | P1 | pendente | |
| R10 | Passagem multipágina | 1 | — | AC-03 | P1 | pendente | |
| R11 | Gates backend reproduzíveis | 1 | — | — | P1 | pendente | |
| R12 | Engine Node suportado | 4 | aprovação de dep. | — | P2 | pendente | |
| R13 | Blobs órfãos | 1 | — | — | P2 | pendente | |
| R14 | OCR/Docling real no gate | 4 | R11 | AC-01, AC-03, AC-16 | P2 | pendente | |
| R15 | Dimensão no adapter | 1 | — | AC-14 | P2 | pendente | |
| R16 | Fuzzy search trigrama | 4 | dataset/T19 | — | P2 | pendente | |
| R17 | Falha do reranker: prova negativa | 1 | — | — | P2 | pendente | |
| R18 | Profundidades E2E | 3 | R02, R03, R04 | AC-06, AC-11 | P2 | pendente | |
| R19 | `quote` sem generator no HTTP | 2 | R02 | AC-08 | P1 | pendente | |
| R20 | Erros tipados no HTTP/SSE | 3 | R07 | AC-14 | P1 | pendente | |
| R21 | Contrato de readiness | 4 | T20 | AC-18 | P3 | pendente | |
| R22 | Matriz de evidências | 4 | R01–R20 | — | P1 | pendente | |

## Ondas e dependências

- **Onda 1** (independentes): R01, R02, R05, R06, R08, R10, R11, R13, R15, R17
- **Onda 2** (após R02): R03, R04, R19
- **Onda 3** (após resposta/recuperação): R07, R09, R18, R20
- **Onda 4** (gates e evidências): R12, R14, R16, R21, R22

Ordem mínima para aprovar: R01 → R02 → R03 → R04 → R05 → R06 → R19 → gates → R22
