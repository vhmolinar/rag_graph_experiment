# Revisão consolidada independente — T01 a T14

Data: 2026-09-02  
Branch: `main`  
Commit revisado: `703aec1`  
Escopo: implementação acumulada das tarefas T01 a T14  
Resultado: **reprovado**

Este documento substitui os pareceres e respostas anteriormente mantidos em
`docs/rag/review_rounds/`. Ele confronta a implementação acumulada com:

- `docs/rag/SPECIFICATION.md`;
- `docs/rag/NOTES.md`;
- `docs/rag/TASKS.md`;
- `docs/rag/REVIEW_CHECKLIST.md`;
- `docs/rag/EVIDENCE.md`.

O resultado “reprovado” não significa que a base de código deva ser descartada.
Persistência, isolamento do domínio, armazenamento de artefatos, busca lexical,
busca vetorial, RRF, adapters HTTP e boa parte dos contratos possuem implementação
sólida. A reprovação decorre de lacunas funcionais no fluxo integrado: o sistema
registra estratégias que não executa, não usa o índice hierárquico, perde uma
limitação obrigatória na fronteira HTTP, admite inferências sem julgamento
semântico e possui um caso de perda de fidelidade literal em EPUB.

---

## 1. Escopo e método

### 1.1 O que foi revisado

- domínio Python;
- migrations e repositories PostgreSQL/pgvector;
- ingestão, OCR, representação canônica, chunking e indexação;
- adapters de embedding, reranking, geração, planejamento, enriquecimento e
  verificação;
- busca lexical e vetorial;
- planejamento, montagem de contexto, `quote` e `dissertative`;
- API FastAPI, SSE, cancelamento, rate limiting, CORS, headers e ranges;
- testes unitários, de contrato e de integração;
- manifests, lockfiles e controles de cadeia de dependências;
- documentação de evidências.

### 1.2 O que não foi revisado como implementado

As tarefas T15–T20 ainda não fazem parte do código concluído. Portanto:

- contexto conversacional autônomo permanece pendente;
- frontend funcional permanece pendente;
- leitor PDF.js permanece pendente;
- rastreabilidade/telemetria completa permanece pendente;
- benchmark permanece pendente;
- Docker Compose operacional permanece pendente.

Esses itens não são atribuídos como regressões das T01–T14, mas impedem que
critérios globais dependentes deles sejam marcados como concluídos.

### 1.3 Evidências executadas

No host macOS da revisão:

| Comando | Resultado |
|---|---|
| `git status --short --branch` | `main`, árvore inicialmente limpa |
| `make lock` | passou |
| frontend `npm ci` | passou; 0 vulnerabilidades; emitiu warning de engine do `jsdom` |
| frontend lint | passou |
| frontend format-check | passou |
| frontend typecheck | passou |
| frontend Vitest | 1 arquivo / 1 teste passou |
| scanner de IOCs | passou; nenhum indicador bloqueado |
| testes unitários focalizados via `.venv` preexistente | 96 passaram, 1 ignorado |
| testes PostgreSQL focalizados via Docker e `.venv` | 89 passaram, 1 ignorado |
| integração CLI de ingestão focalizada | 6 passaram |

### 1.4 Limitação de reprodução

`backend/pyproject.toml` restringe o lock a:

```toml
environments = ["sys_platform == 'linux'"]
```

Como consequência, no macOS:

```text
make lint
make typecheck
make test-unit
make test-integration
make test-contract
make audit
```

abortam no primeiro `uv run` com:

```text
The current Python platform is not compatible with the lockfile's supported
environments: sys_platform == 'linux'
```

A restrição foi registrada para permitir a correção de `transformers` no alvo
Linux, mas tornou obsoletas as evidências antigas que afirmavam executar os
gates backend no macOS.

---

## 2. Veredito por tarefa

| Tarefa | Resultado | Motivo principal |
|---|---|---|
| T01 | aprovado com ressalvas | configuração existe, mas gates backend deixaram de ser reproduzíveis no host de desenvolvimento macOS |
| T02 | aprovado | domínio tipado, validado e isolado de frameworks |
| T03 | aprovado | schema, migrations, constraints, índices e repositories parametrizados |
| T04 | aprovado | store por hash, atomicidade por objeto, ranges e proteção de caminho |
| T05 | aprovado com ressalvas | E2E real de OCR/Docling é opt-in; blobs podem ficar órfãos após rollback relacional |
| T06 | reprovado | chunk parcial de EPUB pode perder o texto original citável |
| T07 | aprovado com ressalvas | núcleo de adapters é bom; validação absoluta de dimensão fica fora do adapter |
| T08 | aprovado com ressalvas | funcionalidade lexical passa; caminho fuzzy real não usa o índice trigrama declarado |
| T09 | aprovado com ressalvas | recuperação principal passa; rastreabilidade completa e testes negativos têm lacunas |
| T10 | reprovado | estratégias são classificadas e exibidas, mas não governam a recuperação |
| T11 | reprovado | índice hierárquico é construído, mas não participa de consultas |
| T12 | aprovado com ressalvas | `quote` e contexto funcionam; faltam provas comportamentais integradas importantes |
| T13 | reprovado | inferência sem evidência é aceita por vacuidade, sem julgamento semântico |
| T14 | reprovado | limitações comparativas são descartadas antes da resposta HTTP; cancelamento é incompleto |

---

## 3. Matriz dos critérios de aceitação

Esta matriz representa o estado acumulado após T14, não a expectativa final
após T20.

| AC | Estado | Fundamentação |
|---|---|---|
| AC-01 | passa | hash único, reingestão idempotente, deduplicação do store |
| AC-02 | passa | obras e edições distintas preservadas no domínio e banco |
| AC-03 | falha/parcial | PDF possui boa evidência; EPUB parcial pode perder fidelidade; leitor T17 ausente |
| AC-04 | passa | frase exata, acentos, stemming e termos em PostgreSQL real |
| AC-05 | passa | paráfrase controlada recuperada por cosseno |
| AC-06 | passa no escopo atual | rankings lexical, vetorial, RRF e reranking persistidos |
| AC-07 | passa com evidência incompleta | filtros chegam ao reranker; fluxo usa fonte duplicada para filtro efetivo |
| AC-08 | passa com evidência incompleta | contrato `quote` não possui prosa; falta spy no fluxo HTTP completo |
| AC-09 | falha | claim inferencial sem evidência não é semanticamente julgada |
| AC-10 | passa | ausência de contexto e baixa cobertura forçam abstenção |
| AC-11 | falha | limitação calculada pelo serviço é descartada na API |
| AC-12 | falha no comportamento integrado | summaries apontam a passagens, mas não ajudam a recuperar regiões |
| AC-13 | pendente | T15 |
| AC-14 | passa com evidência incompleta | erros tipados existem; caminhos HTTP de timeout/verificação não estão cobertos |
| AC-15 | parcial | T18; versões dissertativas e identidade completa da execução não chegam ao `AnswerRun` |
| AC-16 | parcial | T18; logs próprios são redigidos, traces ainda não existem |
| AC-17 | pendente | T18 |
| AC-18 | passa com ressalvas | controles HTTP principais existem; cancelamento e readiness têm lacunas |
| AC-19 | pendente | T19 |
| AC-20 | pendente | T20 |

---

## 4. Achados bloqueadores

### B01 — Estratégias declaradas não governam a recuperação

Severidade: **crítica**  
Tarefas afetadas: T09, T10 e T14  
Requisitos: SPEC §8.3; TASKS T10; checklist §9  
Critérios: AC-05, AC-06, AC-11, AC-15

#### Evidência

`PlannerService.plan()` resolve e registra:

- `literal`;
- `hybrid`;
- `expanded`;
- `automatic`.

Entretanto, `QueryExecutor._retrieve()` chama sempre o mesmo
`RetrievalService.retrieve()`, passando apenas:

- `plan.lexical_query`;
- `plan.semantic_query`;
- filtros;
- profundidade.

`RetrievalService.retrieve()` sempre executa:

1. busca lexical;
2. criação/reuso da versão de embedding;
3. embedding da consulta;
4. busca vetorial;
5. RRF;
6. reranking.

Não existe branch por `plan.strategy` em `application/search.py`,
`api/query_runner.py` ou repositories.

#### Violações concretas

1. Uma consulta `literal` ainda chama o provider de embedding.
2. Uma consulta `literal` ainda executa busca vetorial.
3. Uma consulta `literal` ainda faz RRF.
4. Uma consulta `literal` ainda chama o reranker.
5. A escolha exibida ao cliente não descreve o algoritmo executado.
6. A justificativa registrada não é suficiente para reproduzir a execução,
   porque estratégias diferentes executam o mesmo caminho.

#### Impacto

- contrato de estratégia enganoso;
- chamada indevida a modelos em busca literal;
- latência e custo não correspondem à estratégia;
- falha de embedding/reranker pode quebrar uma busca que deveria ser apenas
  literal;
- resultados de benchmark por estratégia seriam inválidos.

#### Correção necessária

Criar uma política executável de estratégia. A orquestração deve selecionar
explicitamente:

| Estratégia | Lexical | Vetorial | RRF | Rerank | Expansão |
|---|---:|---:|---:|---:|---:|
| literal | sim | não | não | opcional apenas se aprovado como textual | não |
| hybrid | sim | sim | sim | sim | não |
| expanded | sim | sim | sim | sim | sim |

Se o reranking também for desejado em `literal`, isso precisa ser decidido e
documentado sem contradizer a SPEC, que hoje define literal como FTS e
similaridade textual.

#### Testes obrigatórios

- provider de embedding que falha se chamado em `literal`;
- provider de reranking que falha se chamado em `literal`, salvo decisão
  contrária aprovada;
- `hybrid` prova exatamente uma consulta lexical e uma vetorial;
- estratégia persistida coincide com estágios presentes em `AnswerRun`;
- falha de modelo sem relação com `literal` não quebra busca literal.

---

### B02 — Estratégia `expanded` não executa subperguntas, aliases ou conceitos

Severidade: **alta**  
Tarefas afetadas: T10 e T14  
Requisitos: SPEC §8.2–§8.3; TASKS T10  
Critérios: AC-05, AC-11, AC-15

#### Evidência

`PlannerService` popula em `QueryPlan`:

- `subquestions`;
- `aliases`;
- `concept_labels`;
- uma possível substituição de `semantic_query`.

No restante do código:

- `subquestions` não são recuperadas;
- `aliases` não alteram a consulta lexical nem a vetorial;
- `concept_labels` não consultam o índice de conceitos;
- não há fusão de resultados de múltiplas consultas;
- não há orçamento por subpergunta;
- não há rastreabilidade de ranking por expansão.

Assim, o plano pode declarar expansão bem-sucedida sem executar a expansão.

#### Impacto

- consultas conceituais e comparativas perdem cobertura;
- a estratégia automática escolhe `expanded` com justificativa falsa;
- subperguntas geradas consomem modelo sem produzir efeito;
- AC-11 fica mais frágil porque a busca não tenta cobrir aspectos comparativos.

#### Correção necessária

Implementar um executor de expansão separado do planejador:

1. validar e deduplicar consulta principal, subperguntas e aliases;
2. aplicar um orçamento total versionado;
3. executar busca lexical/vetorial por expansão;
4. preservar a origem de cada candidato;
5. fundir resultados sem contar duplicatas várias vezes indevidamente;
6. aplicar filtros em todas as consultas;
7. reranquear apenas candidatos permitidos;
8. registrar todas as consultas, scores e posições.

#### Testes obrigatórios

- uma evidência recuperável apenas por alias aparece em `expanded`, mas não em
  `hybrid`;
- uma evidência recuperável apenas por subpergunta aparece no resultado;
- a mesma passagem recuperada por várias expansões é deduplicada;
- obra excluída não aparece em nenhuma expansão;
- falha de uma expansão segue política explícita e não vira sucesso silencioso;
- orçamento total não é multiplicado sem limite pelo número de subperguntas.

---

### B03 — Índice hierárquico é produzido, mas nunca usado por consultas

Severidade: **alta**  
Tarefas afetadas: T10, T11, T12 e T14  
Requisitos: SPEC §7.4 e §8.7; TASKS T11  
Critérios: AC-11 e AC-12

#### Evidência

T11 implementa:

- summaries de seção;
- summaries de capítulo;
- summary de edição;
- conceitos;
- aliases;
- evidências de conceitos;
- repositories que descem de summary/conceito para passagens.

T10 marca `plan.needs_hierarchical=True` para consultas conceituais e
comparativas.

Porém:

- `needs_hierarchical` não possui consumidor operacional;
- `RetrievalService` não consulta summaries;
- `RetrievalService` não consulta conceitos;
- `ContextService` recebe apenas candidatos do reranking comum;
- nenhuma etapa seleciona nó hierárquico, desce para passagens e reranqueia.

#### Impacto

O sistema paga o custo de enriquecimento, persiste os dados e expõe a intenção
de usá-los, mas responde como um RAG baseado apenas em chunks. A proposta de
“compreensão abstrata” distribuída não está implementada no fluxo real.

#### Correção necessária

Adicionar um `HierarchicalRetrievalService` ou estágio equivalente:

1. selecionar summaries/conceitos relevantes;
2. aplicar filtros antes e depois da seleção de nós;
3. resolver somente suportes da execução de indexação/enriquecimento vigente;
4. descer para passagens originais;
5. unir essas passagens aos candidatos lexical/vetorial;
6. reranquear passagens, nunca summaries;
7. manter summaries fora de `EvidenceRef`;
8. registrar nó selecionado e passagem descendente para auditoria.

#### Testes obrigatórios

- pergunta conceitual cuja passagem de suporte não compartilha os termos da
  pergunta é localizada via summary/conceito;
- summary nunca aparece em `QuoteResponse`;
- summary nunca aparece em `GeneratedAnswer.evidence_ids`;
- exclusão de obra impede seus summaries, conceitos e passagens;
- enriquecimento histórico inativo não participa;
- nó sem suporte não produz candidato;
- falha ao descer um nó falha fechada.

---

### B04 — Limitação comparativa é descartada pela API

Severidade: **crítica**  
Tarefas afetadas: T13 e T14  
Requisitos: SPEC §8.6, §9.3; TASKS T13/T14; checklist §12  
Critério: AC-11

#### Evidência

`DissertativeService.answer()` retorna `DissertativeAnswer`, que contém:

- `answer`;
- `limitations`;
- `verification`;
- IDs de versões.

`QueryExecutor._dissertate()` persiste:

```python
response=diss.answer
verification=diss.verification
```

Ele não persiste `diss.limitations`.

`QueryState` também não possui campo de limitações. Portanto, a limitação fixa
produzida quando uma comparação usa uma única obra morre entre a camada de
aplicação e a fronteira HTTP.

#### Impacto

Uma pergunta comparativa pode retornar `succeeded` e uma resposta baseada em
uma única obra sem informar ao cliente que a comparação está incompleta.
Esse é exatamente o cenário proibido por AC-11.

#### Correção necessária

Definir um resultado persistível único para o modo dissertativo. Opções:

1. persistir `DissertativeAnswer` completo; ou
2. adicionar `limitations` tipadas ao envelope de resultado/`AnswerRun`; ou
3. criar um `DissertativeResponse` de domínio que contenha resposta,
   limitações e verificação.

Não recolocar texto livre do gerador em `GeneratedAnswer.limitations`; a
decisão de derivar limitações no serviço deve ser preservada.

#### Testes obrigatórios

- API comparativa com uma obra retorna limitação;
- mesma consulta via GET e SSE retorna a mesma limitação;
- duas ou mais obras não geram a limitação de fonte única;
- limitação persistida sobrevive a reload do `AnswerRun`;
- texto extra `limitations` vindo do provider continua descartado.

---

### B05 — Claim inferencial sem evidência passa sem julgamento semântico

Severidade: **alta**  
Tarefa afetada: T13  
Requisitos: SPEC §2, §9.3 e §9.4; checklist §12  
Critério: AC-09

#### Evidência

O domínio permite uma claim com:

```text
inference = true
evidence_ids = ()
```

Em `assess_claims()`:

```python
supported = True
for evidence_id in claim.evidence_ids:
    ...
```

Quando a coleção está vazia, o loop não executa e `supported` permanece
`True`. O verificador não julga nenhuma relação entre a inferência e o contexto.
`_acceptable()` então aceita a resposta.

#### Impacto

O gerador pode apresentar qualquer conhecimento externo como “inferência” e
contornar a verificação semântica. A flag torna a ausência de suporte visível,
mas não prova que a inferência deriva do acervo. Isso conflita com o princípio
de que o sistema só responde com conhecimento sustentado pelo acervo.

#### Decisão necessária

Antes da correção, definir um dos contratos:

1. inferência deve citar evidências-base, mesmo quando nenhuma delas sustenta
   diretamente a formulação; ou
2. o verificador deve receber a claim inferencial e todas as evidências do
   contexto para julgar “derivável/não derivável”; ou
3. inferências sem base são removidas, nunca aceitas.

A opção 1 é a mais simples e auditável: manter `inference=true`, mas exigir ao
menos um `evidence_id` como base e distinguir “suporte direto” de
“base inferencial” no veredicto.

#### Testes obrigatórios

- inferência arbitrária sem evidência é rejeitada;
- inferência com base irrelevante é rejeitada;
- inferência derivável é aceita e marcada;
- timeout ao verificar inferência falha fechado;
- cobertura não trata coleção vazia como sucesso por vacuidade.

---

### B06 — Chunk parcial de EPUB pode perder o texto original

Severidade: **alta**  
Tarefa afetada: T06  
Requisitos: SPEC §7.2–§7.3; TASKS T06; checklist §7–§8  
Critério: AC-03

#### Evidência

Em `domain/chunking.py`, `_original_text_of` preserva
`block_original_text` quando o chunk cobre o bloco inteiro. Quando cobre apenas
algumas sentenças do bloco, a função recompõe:

```python
" ".join(sentence.text ...)
```

`sentence.text` é o texto normalizado. Para EPUB, onde não há offsets de
página e `Passage.original_text` é a fonte literal, normalização pode remover:

- hífen de quebra de linha;
- whitespace significativo;
- pontuação/Unicode;
- diferenças entre `original_text` e `text`.

#### Cenário mínimo

1. bloco EPUB com duas ou mais sentenças;
2. `original_text` diferente de `text`;
3. janela pequena que selecione apenas parte das sentenças;
4. passagem citável recebe texto normalizado, não fatia exata do original.

#### Impacto

O modo `quote` pode apresentar uma string que não é literalmente a fonte,
violando o contrato de citação.

#### Correção necessária

Persistir offsets dentro do bloco original ou segmentar sentenças mantendo:

- início/fim no texto original;
- início/fim no texto normalizado;
- mapeamento explícito entre ambos.

Não reconstruir texto original juntando sentenças normalizadas.

#### Testes obrigatórios

- EPUB com hífen de quebra e chunk parcial;
- EPUB com whitespace e Unicode divergentes;
- chunk completo preserva comportamento atual;
- várias sentenças do mesmo bloco em chunks distintos recompõem exatamente
  suas respectivas fatias originais;
- `quote` retorna o literal, não o normalizado.

---

## 5. Achados importantes

### I01 — Cancelamento não é verificado em todas as fronteiras

Severidade: **alta**  
Tarefa afetada: T14  
Requisito: TASKS T14 “cancelamento interrompe trabalho”  
Critério: AC-18

O executor verifica cancelamento:

- antes de planejamento;
- depois de planejamento;
- depois de recuperação.

Não verifica:

- após montagem de contexto e antes de `quote`;
- após montagem de contexto e antes do generator;
- entre geração e verificação;
- durante espera de provider;
- antes de persistir sucesso.

O teste atual bloqueia `embed_query`, solicita cancelamento, libera o provider e
observa o flag na fronteira seguinte. Ele não cobre cancelamento na fase mais
cara: geração/verificação.

#### Correção

- verificar o flag antes e depois de cada estágio;
- decidir se providers devem receber cancelamento cooperativo;
- considerar cancelar a task ativa e traduzir `CancelledError` em estado
  persistido `cancelled`;
- garantir que uma resposta concluída depois do pedido não sobrescreva
  `cancelled`.

#### Testes

- cancelar durante contexto;
- cancelar durante generator;
- cancelar entre generator e verifier;
- cancelar durante verifier;
- corrida cancelamento versus sucesso;
- SSE termina uma única vez com estado coerente.

---

### I02 — `plan.effective_filters` não é a fonte usada pelo executor

Severidade: **média**  
Tarefas afetadas: T10 e T14  
Critério: AC-07

O planner calcula e persiste `effective_filters`, mas o executor recalcula:

```python
merge_filters(request.explicit_filter(), plan.inferred_filters)
```

Hoje o resultado tende a ser equivalente. Porém existem duas fontes de verdade.
Uma mudança em regras de merge, chips confirmados ou reexecução de plano
persistido pode produzir divergência.

#### Correção

Passar exclusivamente `plan.effective_filters` para todos os estágios e
persistir exatamente esse escopo.

#### Testes

- spy do retrieval recebe o mesmo objeto/valor do plano;
- plano persistido e retomado não depende do request original;
- filtros confirmados/editados têm contrato explícito;
- nenhuma etapa recompõe filtros por conta própria.

---

### I03 — Versões dissertativas são registradas e depois descartadas

Severidade: **alta**  
Tarefas afetadas: T13, T14 e futura T18  
Critério: AC-15

`DissertativeAnswer` retorna IDs de:

- prompt de geração;
- prompt de verificação;
- endpoint de geração;
- endpoint de verificação;
- política de verificação.

`QueryExecutor` não copia esses IDs para `AnswerRun.versions`.
`ContextPolicyVersion` também não é associada ao run final.

Além disso, `VersionSet` não contém hoje:

- `context_policy_version_id`;
- `verification_policy_version_id`;
- `verifier_endpoint_version_id`;
- identidade de `IndexRun`;
- identidade de execução de enriquecimento.

#### Correção

Expandir `VersionSet`, a migration e o fluxo de persistência. A atualização
deve ser append-only e validada antes de transição terminal.

#### Testes

- reload de uma consulta contém todas as versões;
- uma configuração diferente cria nova identidade;
- nenhuma versão já registrada pode ser alterada;
- resposta só transiciona para sucesso se o conjunto mínimo estiver completo.

---

### I04 — Versão do verifier usa o nome do generator

Severidade: **alta**  
Tarefa afetada: T13/T14  
Critério: AC-15

`create_app()` obtém apenas `generator_model_name`. O serviço recebe um único
`model_name` e o usa ao registrar os dois `ModelEndpointVersion`:

- generator;
- verifier.

Se `GENERATOR_MODEL` e `VERIFIER_MODEL` forem diferentes, o registro do
verifier fica incorreto.

#### Correção

Cada provider deve expor uma identidade de endpoint/modelo versionável ou a
composição deve injetar duas identidades separadas.

#### Testes

- generator `model-a`, verifier `model-b`;
- registros persistidos mantêm nomes distintos;
- URLs/params que afetam comportamento fazem parte da identidade sem guardar
  segredo;
- troca apenas do verifier cria nova versão do verifier.

---

### I05 — Endpoint de passagem perde metadados multipágina

Severidade: **média**  
Tarefa afetada: T14  
Critério: AC-03

`EvidenceRef` e `CitablePassage` carregam:

- página inicial e final;
- rótulo inicial e final;
- IDs de ambas as páginas;
- offsets relativos às duas páginas.

`PassageDetail`, retornado por
`GET /editions/{edition_id}/passages/{passage_id}`, expõe apenas:

- `physical_page`;
- `printed_label`;
- `char_start`;
- `char_end`.

Para uma passagem multipágina, `char_end` fica sem a identidade da página à
qual pertence.

#### Correção

Alinhar `PassageDetail` ao contrato citável completo.

#### Testes

- endpoint de passagem multipágina;
- início e fim em páginas distintas;
- offsets invertidos entre páginas continuam válidos;
- resposta permite recompor o trecho exato.

---

### I06 — Ambiente Linux-only quebrou os gates documentados para macOS

Severidade: **média**  
Tarefa afetada: T01  
Requisito: instalação reproduzível e comandos uniformes

A restrição pode ser aceitável para o alvo de produção, mas o repositório é
usado em macOS e o Makefile não oferece um caminho Linux transparente.

#### Alternativas

1. target de desenvolvimento em container Linux;
2. separar lock do runtime Linux e lock de ferramentas portáveis;
3. resolver a incompatibilidade `docling-core`/`transformers` sem restringir
   todo o projeto;
4. documentar explicitamente que backend só roda em Linux e fornecer wrapper
   reproduzível.

#### Testes

- `make lint`, `typecheck`, unit e contract em host macOS via container;
- `make lock` não modifica lockfile;
- mesma versão de Python/uv registrada no relatório.

---

### I07 — `jsdom` declara engine superior à instalada

Severidade: **baixa/média**  
Tarefa afetada: T01  
Requisito: instalação reproduzível

`npm ci` passou, mas emitiu:

```text
jsdom@30.0.1 requires node ^22.22.2 || ^24.15.0 || >=26.0.0
current node v22.21.1
```

Os testes atuais passaram, mas a combinação está fora da matriz suportada pelo
pacote.

#### Correção

- elevar o runtime Node para versão suportada; ou
- usar versão de `jsdom` compatível, mediante aprovação de dependência.

Não alterar dependência sem aprovação explícita.

---

### I08 — Falha transacional pode deixar blob órfão

Severidade: **média**  
Tarefa afetada: T05  
Requisito: CLI não publica processamento parcial

O artefato é gravado no filesystem antes da transação relacional. Se a
persistência de seção/página/edição falhar, não há edição publicada, mas o
objeto por hash pode permanecer órfão.

O store possui `audit()`, mas o teste de falha parcial verifica apenas o banco.

#### Correção

Escolher e documentar:

- órfão é permitido e coletado por GC verificável; ou
- compensação remove objeto recém-criado se nenhuma referência existir.

Como o store é deduplicado, compensação deve evitar remover blob já usado por
outra edição/operação concorrente.

#### Testes

- falha após `store.put`;
- blob novo órfão;
- blob deduplicado preexistente não é removido;
- auditoria/GC encontra e remove somente órfãos seguros.

---

### I09 — OCR real e Docling completo não fazem parte do gate padrão

Severidade: **média**  
Tarefa afetada: T05  
Critérios: AC-01, AC-03, AC-16

Os testes com `RAG_OCR_E2E=1` e `RAG_DOCLING_E2E=1` são opt-in. O gate padrão
permite regressão no caminho real enquanto stubs continuam verdes.

#### Correção

Criar um job Linux dedicado, determinístico e contabilizado:

- modelos/cache pinados;
- fixture legal e pequena;
- OCR real;
- Docling real;
- ingestão até PostgreSQL;
- scanner de logs;
- timeout explícito.

---

### I10 — Adapter de embedding não conhece a dimensão esperada

Severidade: **média**  
Tarefa afetada: T07  
Critério: AC-14

O adapter valida:

- finitude;
- cardinalidade;
- consistência de dimensão entre itens.

Ele pode aceitar lote inteiro com 512 dimensões quando o sistema espera 1024.
Indexação/repository rejeitam depois, portanto há falha fechada, mas não na
fronteira do provider.

#### Correção

Associar `EmbeddingVersion.dimensions` ao provider e validar cada resposta no
adapter.

#### Testes

- resposta 512 versus versão 1024;
- consulta e documentos;
- erro antes de qualquer persistência;
- mensagem sanitizada.

---

### I11 — Caminho fuzzy real não usa o índice trigrama

Severidade: **média**  
Tarefa afetada: T08; calibração em T19  
Requisito: checklist §9

A busca fuzzy compara cada termo com cada palavra obtida por
`unnest(regexp_split_to_array(...))`. Esse formato não usa o GIN trigrama
criado sobre o texto integral. O teste de índice trigrama usa uma consulta
diferente da query de produção.

#### Impacto

Funcionalidade está correta em fixtures pequenas, mas o custo pode crescer como
scan por passagem/palavra.

#### Correção

Medir com `EXPLAIN ANALYZE` no corpus representativo. Se inadequado:

- adicionar estrutura indexável por tokens; ou
- etapa indexada de pré-seleção seguida de comparação palavra a palavra; ou
- rever o desenho com evidência e aprovação.

---

### I12 — Reranker falho não possui prova negativa de persistência

Severidade: **baixa**  
Tarefa afetada: T09

O código só salva rankings após reranking, o que indica comportamento correto.
O teste de falha, porém, apenas verifica a exceção e não recarrega o
`AnswerRun`.

Adicionar asserção de que:

- candidatos não foram parcialmente anexados;
- política/versões não criam um run aparentemente concluído;
- status final é falha no fluxo API.

---

### I13 — Políticas de profundidade carecem de prova integrada

Severidade: **média**  
Tarefas afetadas: T12 e T13

Há unit tests de defaults e monotonicidade. A integração de contexto usa
principalmente `standard`; não demonstra o mesmo corpus produzindo diferenças
observáveis entre `brief`, `standard` e `deep`.

#### Testes

- mesmo ranking com as três profundidades;
- quantidade de candidatos/evidências;
- tamanho de expansão parental;
- número máximo de iterações;
- timeout deep;
- tamanho final e limitações.

---

### I14 — Ausência de geração em `quote` é provada estruturalmente, não no HTTP

Severidade: **média**  
Tarefas afetadas: T12 e T14  
Critério: AC-08

O desenho atual é seguro: `ContextService` não recebe generator. Contudo, o
teste usa inspeção de assinatura. Um futuro erro no `QueryExecutor` poderia
chamar generator antes/depois do contexto sem quebrar esse teste.

#### Teste obrigatório

Executar `POST /queries` em modo `quote` com generator/verifier que falham
imediatamente se chamados; exigir sucesso com trecho literal.

---

### I15 — Falhas tipadas de modelo não são cobertas no fluxo HTTP

Severidade: **média**  
Tarefa afetada: T14  
Critério: AC-14

Há teste para `RuntimeError` virar `INTERNAL_ERROR`, mas faltam integrações para:

- `ModelTimeoutError` → `MODEL_TIMEOUT`;
- `ModelUnavailableError` → `MODEL_UNAVAILABLE`;
- `ModelResponseError` → `MODEL_INVALID_RESPONSE`;
- `VerificationError` → `VERIFICATION_FAILED`;
- erro terminal equivalente em GET e SSE.

---

### I16 — Readiness 503 não usa envelope padrão

Severidade: **baixa**  
Tarefa afetada: T14  
Critério: AC-18

Falha de readiness retorna `{"status":"unavailable"}` em vez do envelope de
erro com código e `request_id`. É aceitável tratar health como contrato
especial, mas essa exceção precisa ser explícita na especificação ou alinhada
ao padrão.

---

### I17 — Evidências documentais ficaram obsoletas após consolidação

Severidade: **média**  
Tarefa: documentação/revisão

`EVIDENCE.md` contém:

- referências aos pareceres removidos de `review_rounds`;
- afirmações históricas substituídas;
- T01 marcado como concluído com execução macOS hoje impossível;
- afirmações de cobertura completa para AC-11 e AC-12 contrariadas por esta
  revisão.

Não alterar a matriz para “parecer verde”. Ela deve ser corrigida somente para
refletir:

- achados atuais;
- testes reais;
- estados `passa`, `parcial`, `falha` e `pendente`;
- este parecer consolidado.

---

## 6. Pontos conformes comprovados

### 6.1 Arquitetura

- domínio não importa FastAPI, ORM, Docling ou SDK de modelos;
- LangChain não controla domínio ou evidências;
- PostgreSQL/pgvector permanecem a fonte estruturada;
- nenhum banco vetorial adicional, grafo, fila distribuída ou Kubernetes;
- nenhuma autenticação caseira;
- arquivos ficam fora do banco, endereçados por hash.

### 6.2 Cadeia de dependências

- manifests diretos usam pinagem exata;
- lockfiles existem;
- Axios não é dependência;
- nenhum `plain-crypto-js`;
- nenhuma versão bloqueada de Axios;
- nenhuma referência ao C2 bloqueado;
- `npm ci` reportou zero vulnerabilidades.

### 6.3 Persistência

- cadeia Alembic linear até `0010`;
- constraints compostas protegem edição/página/seção;
- versão de embedding é compatível com `vector(1024)`;
- versões são imutáveis no banco;
- `AnswerRun` usa revisão otimista;
- SQL de usuário é parametrizado.

### 6.4 Recuperação

- FTS português, acentos, frase e termos;
- vetores por cosseno;
- lista lexical e vetorial separadas;
- RRF determinístico;
- reranker altera ordem em caso controlado;
- filtro de obra é aplicado antes do reranker;
- conjunto ativo de indexação é respeitado.

### 6.5 Geração e segurança

- `quote` não possui campo de prosa;
- IDs inexistentes não são liberados;
- contradição prevalece sobre `supported`;
- resposta dissertativa usa blocos ligados às claims;
- canais anteriores de prosa livre foram fechados;
- verificador não devolve detalhe factual livre;
- baixa cobertura força abstenção;
- falha do verifier falha fechada;
- API possui request ID, CORS explícito, token bucket e headers;
- 429 mantém CORS, request ID, headers e `Retry-After`;
- erros inesperados não expõem stack trace ao cliente.

---

## 7. Plano de correção dividido para agentes

Cada item abaixo foi desenhado para permitir atribuição independente. Agentes
não devem alterar requisitos ou critérios de aceitação. Dependências entre
pacotes estão explicitadas.

### R01 — Corrigir fidelidade de EPUB em chunks parciais

Prioridade: P0  
Responsável sugerido: agente de chunking/proveniência  
Depende de: nenhuma  
Arquivos principais:

- `backend/src/rag/domain/chunking.py`;
- `backend/src/rag/domain/canonical.py`;
- `backend/tests/unit/test_chunking.py`;
- `backend/tests/integration/test_index.py`.

Pronto quando:

- mapeamento original/normalizado é explícito;
- chunk parcial preserva literal exato;
- testes positivos, negativos e multiparciais passam.

---

### R02 — Tornar estratégia `literal` realmente literal

Prioridade: P0  
Responsável sugerido: agente de recuperação  
Depende de: nenhuma  
Arquivos principais:

- `backend/src/rag/application/search.py`;
- `backend/src/rag/api/query_runner.py`;
- `backend/src/rag/domain/retrieval.py`;
- testes de pipeline/API.

Pronto quando:

- embedding e busca vetorial não são chamados;
- estágios persistidos correspondem à estratégia;
- falha dos providers sem uso não afeta a resposta.

---

### R03 — Implementar execução da estratégia `expanded`

Prioridade: P0  
Responsável sugerido: agente de recuperação multiquery  
Depende de: R02  
Arquivos principais:

- novo serviço de expansão em `application/`;
- `application/search.py`;
- `domain/retrieval.py`;
- testes unitários e PostgreSQL.

Pronto quando:

- aliases e subperguntas alteram candidatos;
- deduplicação, orçamento, filtros e rastreabilidade são testados.

---

### R04 — Integrar recuperação hierárquica

Prioridade: P0  
Responsável sugerido: agente de enriquecimento/recuperação  
Depende de: R03 ou contrato de fusão coordenado com R03  
Arquivos principais:

- `infrastructure/repositories/enrichment.py`;
- novo serviço hierárquico;
- `application/search.py`;
- `application/context.py`.

Pronto quando:

- `needs_hierarchical` governa um estágio real;
- summaries/conceitos localizam passagens;
- somente passagens viram evidências.

---

### R05 — Preservar limitações dissertativas na API

Prioridade: P0  
Responsável sugerido: agente de contratos/API  
Depende de: nenhuma  
Arquivos principais:

- `domain/answer.py` ou novo response type;
- `domain/runs.py`;
- migration;
- `api/query_runner.py`;
- `api/schemas.py`;
- testes API.

Pronto quando:

- GET e SSE preservam limitações;
- AC-11 possui integração HTTP com uma e duas obras.

---

### R06 — Fechar bypass de inferências

Prioridade: P0  
Responsável sugerido: agente de verificação  
Depende de: decisão do contrato de inferência  
Arquivos principais:

- `domain/answer.py`;
- `domain/providers.py`;
- `domain/verification.py`;
- `application/dissertative.py`;
- adapter do verifier.

Pronto quando:

- claim inferencial não é aceita por coleção vazia;
- relação inferência/contexto é julgada;
- timeout permanece fail-closed.

---

### R07 — Completar cancelamento

Prioridade: P1  
Responsável sugerido: agente de execução assíncrona/API  
Depende de: coordenar com R05 para transição terminal  
Arquivos principais:

- `api/tasks.py`;
- `api/query_runner.py`;
- `api/routes/queries.py`;
- `api/events.py`;
- integração API.

Pronto quando:

- todos os estágios possuem fronteiras de cancelamento;
- corrida sucesso/cancelamento é determinística;
- SSE emite um terminal.

---

### R08 — Unificar filtro efetivo

Prioridade: P1  
Responsável sugerido: agente de planejamento  
Depende de: nenhuma  
Arquivos principais:

- `api/query_runner.py`;
- testes de planner/executor.

Pronto quando:

- somente `plan.effective_filters` governa a execução;
- filtro persistido coincide com filtro usado.

---

### R09 — Completar rastreabilidade de versões

Prioridade: P1  
Responsável sugerido: agente de persistência/rastreabilidade  
Depende de: R03, R04 e R05 para conhecer contratos finais  
Arquivos principais:

- `domain/runs.py`;
- migration;
- `application/search.py`;
- `application/context.py`;
- `application/dissertative.py`;
- `api/query_runner.py`.

Pronto quando:

- run registra indexação, políticas, endpoints e prompts;
- identidade do verifier é correta e distinta;
- execução pode ser reconstruída.

---

### R10 — Completar metadados multipágina no endpoint

Prioridade: P1  
Responsável sugerido: agente de catálogo/API  
Depende de: nenhuma  
Arquivos principais:

- `api/schemas.py`;
- `api/routes/catalog.py`;
- integração API.

Pronto quando:

- endpoint transporta páginas/labels/IDs de início e fim;
- offsets recompõem trecho.

---

### R11 — Fornecer gates backend reproduzíveis

Prioridade: P1  
Responsável sugerido: agente de tooling/CI  
Depende de: decisão sobre suporte host versus container  
Arquivos principais:

- `Makefile`;
- `backend/pyproject.toml`;
- documentação operacional;
- CI.

Pronto quando:

- macOS consegue executar gates via caminho documentado;
- Linux nativo continua reproduzível;
- lock e audit passam.

Não adicionar ou trocar dependências sem aprovação.

---

### R12 — Resolver engine Node incompatível

Prioridade: P2  
Responsável sugerido: agente frontend/tooling  
Depende de: aprovação se houver mudança de dependência  
Arquivos principais:

- documentação de runtime;
- eventualmente `package.json`/lockfile.

Pronto quando `npm ci` não produz `EBADENGINE`.

---

### R13 — Definir política de blobs órfãos

Prioridade: P2  
Responsável sugerido: agente de ingestão/storage  
Depende de: nenhuma  
Arquivos principais:

- `application/ingest.py`;
- `infrastructure/artifacts.py`;
- testes de falha.

Pronto quando a política é segura sob deduplicação e concorrência.

---

### R14 — Promover OCR/Docling real a gate

Prioridade: P2  
Responsável sugerido: agente de CI/ingestão  
Depende de: R11  
Arquivos principais:

- testes E2E existentes;
- CI;
- documentação de cache/modelos.

Pronto quando o job real é repetível e contabiliza skips.

---

### R15 — Validar dimensão no adapter de embedding

Prioridade: P2  
Responsável sugerido: agente de adapters  
Depende de: nenhuma  
Arquivos principais:

- `adapters/embedding_adapter.py`;
- settings/identidade do provider;
- contract tests.

Pronto quando 512 versus 1024 falha na fronteira HTTP.

---

### R16 — Medir e corrigir fuzzy search

Prioridade: P2  
Responsável sugerido: agente PostgreSQL/performance  
Depende de: dataset representativo ou T19  
Arquivos principais:

- `repositories/search.py`;
- migrations se justificadas;
- `test_index_usage.py`;
- benchmark.

Pronto quando a query real possui plano e latência aceitáveis.

---

### R17 — Fortalecer testes de falha do reranker

Prioridade: P2  
Responsável sugerido: agente de testes de recuperação  
Depende de: nenhuma

Pronto quando falha prova ausência de persistência parcial.

---

### R18 — Testar profundidades ponta a ponta

Prioridade: P2  
Responsável sugerido: agente de contexto/geração  
Depende de: R02–R04 para que estratégias estejam corretas

Pronto quando `brief`, `standard` e `deep` mostram diferenças de política
observáveis e versionadas.

---

### R19 — Provar `quote` sem generator no fluxo HTTP

Prioridade: P1  
Responsável sugerido: agente de testes API  
Depende de: R02

Pronto quando providers sentinela não são chamados em `quote`.

---

### R20 — Cobrir erros tipados no HTTP/SSE

Prioridade: P1  
Responsável sugerido: agente de testes API  
Depende de: R07 para semântica terminal estável

Pronto quando cada erro de modelo/verificação mantém código, mensagem segura e
request ID em GET e SSE.

---

### R21 — Decidir contrato de readiness

Prioridade: P3  
Responsável sugerido: agente de API/operação  
Depende de: T20

Pronto quando health possui exceção documentada ou envelope uniforme.

---

### R22 — Reconstruir matriz de evidências

Prioridade: P1  
Responsável sugerido: agente de documentação/evidências  
Depende de: R01–R20 concluídas ou marcadas honestamente

Arquivos principais:

- `docs/rag/EVIDENCE.md`;
- este parecer;
- saída dos gates.

Pronto quando:

- não há links para arquivos removidos;
- contagens e ambientes são atuais;
- ACs não são promovidos antes da prova;
- comandos possuem exit code e skips contabilizados.

---

## 8. Paralelização recomendada

### Onda 1 — independentes

Podem iniciar simultaneamente:

- R01 — EPUB;
- R02 — estratégia literal;
- R05 — limitações API;
- R06 — contrato de inferência, após decisão curta;
- R08 — filtro efetivo;
- R10 — passagem multipágina;
- R11 — tooling Linux/macOS;
- R13 — blobs órfãos;
- R15 — dimensão de embedding;
- R17 — falha do reranker.

### Onda 2 — integração da recuperação

Após contrato de R02:

- R03 — expanded;
- R04 — hierárquico, coordenado com R03;
- R19 — quote HTTP.

### Onda 3 — execução e rastreabilidade

Após os contratos de resposta/recuperação estabilizarem:

- R07 — cancelamento;
- R09 — versões completas;
- R18 — profundidades E2E;
- R20 — erros HTTP/SSE.

### Onda 4 — gates e evidências

- R12 — runtime Node;
- R14 — OCR/Docling real;
- R16 — performance fuzzy;
- R21 — readiness;
- R22 — matriz final.

---

## 9. Ordem mínima para retirar a reprovação

O projeto não deve ser reapresentado como conforme antes de:

1. R01 corrigir a fidelidade literal de EPUB;
2. R02 tornar `literal` executável;
3. R03 executar expansão de fato;
4. R04 conectar o índice hierárquico;
5. R05 preservar limitações na API;
6. R06 fechar inferências vazias;
7. R19 provar `quote` sem geração no HTTP;
8. executar unit, contract e integração em Linux;
9. atualizar a matriz por R22.

R07, R09, R10 e R20 devem ser tratados antes de considerar T14 plenamente
aprovada. Os demais itens podem permanecer como ressalvas apenas se houver
decisão explícita, evidência e tarefa futura vinculada.

---

## 10. Conclusão

A implementação das T01–T14 possui uma base técnica substancial, mas não
realiza integralmente a arquitetura aprovada. As falhas principais aparecem
nas costuras entre componentes:

- planejamento registra decisões que a recuperação não executa;
- enriquecimento produz um índice que a consulta não usa;
- verificação aceita um caso inferencial sem julgamento;
- a aplicação produz uma limitação que a API elimina;
- o chunker preserva texto original apenas em parte dos casos EPUB;
- o cancelamento não cobre o ciclo completo.

Esses problemas não devem ser resolvidos relaxando critérios, removendo
verificação ou alterando a especificação para refletir o código. A correção
deve fazer o fluxo executado corresponder ao contrato já aprovado, com testes
positivos, negativos, de falha e integração para cada pacote R01–R22.
