# Checklist de julgamento da implementação

Este documento será usado por um agente revisor independente após a implementação.

## 1. Resultado possível

- **Aprovado:** todos os requisitos bloqueadores passam; não há desvio relevante sem aprovação.
- **Aprovado com ressalvas:** requisitos bloqueadores passam, mas existem falhas menores, dívida documentada ou evidências incompletas sem risco à correção.
- **Reprovado:** qualquer requisito bloqueador falha, há desvio arquitetural silencioso, teste crítico ausente ou evidência de resposta não fundamentada.

## 2. Regras de evidência

O revisor deve:

1. inspecionar o diff e o código, não apenas relatórios;
2. executar os comandos documentados;
3. verificar testes negativos e caminhos de falha;
4. usar ao menos um PDF/EPUB de fixture do início ao fim;
5. comparar a matriz do implementador com `AC-01` a `AC-20`;
6. registrar comando, resultado e artefato para cada conclusão;
7. tratar testes ignorados, instáveis ou excessivamente mockados como evidência insuficiente.

## 3. Pré-condições da revisão

- [ ] `SPECIFICATION.md`, `TASKS.md` e `NOTES.md` estão presentes.
- [ ] O implementador entregou inventário de dependências.
- [ ] O implementador entregou matriz requisito-evidência.
- [ ] O ambiente pode ser iniciado com instruções documentadas.
- [ ] Não há segredos reais em arquivos versionados.
- [ ] Desvios possuem decisão e aprovação registradas.

Falha em qualquer item impede aprovação plena.

## 4. Validação automatizada

Executar os comandos equivalentes definidos pelo projeto:

```text
lint
format-check
typecheck
unit-tests
integration-tests
contract-tests
e2e-tests
security-scan
dependency-audit
evaluation-runner
docker-smoke-test
```

Para cada comando:

- [ ] comando exato registrado;
- [ ] código de saída zero;
- [ ] testes executados e ignorados contabilizados;
- [ ] versões de runtime e ferramentas registradas;
- [ ] falhas intermitentes investigadas, não apenas reexecutadas.

## 5. Arquitetura e limites

- [ ] O domínio não importa FastAPI, ORM, Docling ou SDK de modelo.
- [ ] Não há LangChain controlando domínio, evidências ou persistência.
- [ ] PostgreSQL é a fonte dos dados estruturados e vetores.
- [ ] Arquivos são armazenados por hash fora do banco.
- [ ] Não foi introduzido banco vetorial, grafo, fila ou Kubernetes sem aprovação.
- [ ] Itens colaborativos não foram implementados na fase 1.
- [ ] Não foi criada autenticação caseira.
- [ ] Dependências estão pinadas e vieram de fontes oficiais.

Bloqueadores: acoplamento que impeça testes dos contratos, serviço adicional não aprovado ou dependência insegura.

## 6. Segurança da cadeia de dependências

- [ ] Lockfiles correspondem aos manifests.
- [ ] Integridades não foram alteradas manualmente.
- [ ] Auditoria de dependências foi executada.
- [ ] Não há dependências sem versão ou vindas de URLs/forks não aprovados.
- [ ] Não há `plain-crypto-js`.
- [ ] Não há Axios 1.14.1 ou 0.30.4.
- [ ] Se Axios existir, está pinado em uma versão verificada como segura.
- [ ] Não há referência à infraestrutura maliciosa bloqueada pelas regras do workspace.

Qualquer indicador bloqueado implica reprovação imediata e tratamento como incidente.

## 7. Ingestão e proveniência

- [ ] `rag ingest --dry-run` não persiste dados.
- [ ] Reingestão idêntica é idempotente.
- [ ] PDF-texto e EPUB geram schema canônico.
- [ ] OCR é comando separado.
- [ ] Falha parcial não publica edição.
- [ ] Hierarquia e ordem de leitura são preservadas.
- [ ] Duas edições da mesma obra permanecem distintas.
- [ ] Offsets recompõem o trecho original.
- [ ] Arquivos maliciosos não causam path traversal.
- [ ] Conteúdo integral não aparece em logs.

Critérios: AC-01, AC-02, AC-03, AC-16.

## 8. Chunking, embeddings e versões

- [ ] Chunks respeitam seções e frases.
- [ ] Cabeçalho contextual não é confundido com texto citável.
- [ ] Relação filho/pai existe e é testada.
- [ ] Dimensão incompatível do embedding falha antes de gravar.
- [ ] Documento e consulta usam versões compatíveis.
- [ ] Reindexação cria nova versão.
- [ ] Execução registra versão de extração, chunking e embedding.

Critérios: AC-03, AC-12, AC-15.

## 9. Recuperação

### Lexical

- [ ] Frase exata em português é encontrada.
- [ ] Acentos e variações são testados.
- [ ] Termos obrigatórios/excluídos funcionam.
- [ ] SQL é parametrizado.
- [ ] Índices são usados em consultas representativas.

### Semântica

- [ ] Paráfrase sem termos principais recupera evidência.
- [ ] Métrica vetorial está documentada.
- [ ] Filtros são aplicados antes da seleção.

### Fusão e reranking

- [ ] Rankings lexical e vetorial são preservados.
- [ ] RRF possui teste determinístico.
- [ ] Reranker recebe apenas candidatos permitidos.
- [ ] Scores e posições são rastreáveis.
- [ ] Falha do reranker não é mascarada como sucesso.

### Planejamento

- [ ] Estratégia automática registra sua escolha.
- [ ] Estratégias explícitas são respeitadas.
- [ ] Filtros inferidos voltam ao cliente.
- [ ] Filtro explícito prevalece.
- [ ] Obra excluída não aparece em nenhum estágio.
- [ ] Diversidade é adaptativa, não quota cega.

Critérios: AC-04, AC-05, AC-06, AC-07, AC-11.

## 10. Resumos e conceitos

- [ ] Todo summary possui passagens de suporte.
- [ ] Summary ajuda a localizar, mas não é citação final.
- [ ] Conceitos e aliases registram confiança e versão.
- [ ] Um conceito pode ser rastreado até texto original.
- [ ] Item abstrato sem suporte é rejeitado.

Critério: AC-12.

## 11. Modo quote

- [ ] O generator não é chamado.
- [ ] A resposta contém apenas trechos e metadados.
- [ ] Não há paráfrase, tradução ou introdução gerada.
- [ ] Cada trecho abre a edição correta.
- [ ] Página e destaque são corretos.
- [ ] Ordem corresponde ao ranking retornado.

Bloqueador: qualquer texto sintetizado apresentado como citação.

Critérios: AC-03, AC-08.

## 12. Modo dissertative

- [ ] Brief, standard e deep alteram políticas, não apenas uma frase no prompt.
- [ ] Evidências são numeradas e não podem ser inventadas.
- [ ] Cada afirmação tem evidência ou marcação de inferência.
- [ ] Verificador rejeita ID inexistente.
- [ ] Verificador detecta afirmação não sustentada.
- [ ] Verificador não introduz conteúdo novo.
- [ ] Limite de regenerações evita loop.
- [ ] Sem suporte suficiente, há abstenção.
- [ ] Timeout do verificador não libera resposta não verificada.
- [ ] Conhecimento externo do modelo não é usado como fallback.

Bloqueadores: afirmação factual sem suporte, citação falsa ou bypass do verificador.

Critérios: AC-09, AC-10, AC-11, AC-14.

## 13. Sessões e filtros

- [ ] Follow-up é reescrito como pergunta autônoma.
- [ ] Pergunta original e reescrita são registradas.
- [ ] Sessões não vazam contexto entre si.
- [ ] Histórico possui limite.
- [ ] Sessão pode ser excluída.
- [ ] Chips refletem filtros inferidos e podem ser corrigidos.

Critério: AC-13.

## 14. API

- [ ] Prefixo `/api/v1`.
- [ ] Métodos HTTP são explícitos.
- [ ] Body, query e path são tipados e validados.
- [ ] Erros possuem código estável e request ID.
- [ ] Stack traces, SQL e caminhos locais não chegam ao cliente.
- [ ] SSE encerra corretamente em sucesso, erro e cancelamento.
- [ ] Rate limit usa janela deslizante ou token bucket.
- [ ] 429 inclui `Retry-After`.
- [ ] CORS usa origem explícita.
- [ ] Headers de segurança estão presentes.
- [ ] Readiness considera dependências; liveness não causa restart por falha transitória externa.

Critérios: AC-14, AC-18.

## 15. Frontend e leitor

- [ ] Estados loading, streaming, erro, cancelado e abstido são distintos.
- [ ] Modo, profundidade e estratégia são controláveis.
- [ ] Estratégia efetiva é visível.
- [ ] Markdown é sanitizado.
- [ ] URLs de documento não são arbitrárias.
- [ ] PDF.js usa a edição correta.
- [ ] Destaque corresponde ao trecho.
- [ ] EPUB sem página estável informa a limitação.
- [ ] Interface básica é navegável por teclado.

Critérios: AC-02, AC-03, AC-07, AC-10.

## 16. Modelos e falhas

- [ ] Endpoints, modelos e credenciais são configuração.
- [ ] Credenciais vêm de ambiente/secret file.
- [ ] Timeouts variam por operação/profundidade.
- [ ] Retry ocorre apenas em falha transitória idempotente.
- [ ] Há limite de concorrência.
- [ ] Circuit breaker é exercitado.
- [ ] Payload inválido é rejeitado.
- [ ] Não há fallback silencioso sem RAG.
- [ ] Logs não expõem chaves, prompts completos ou livros.

Critérios: AC-14, AC-16.

## 17. Rastreabilidade, privacidade e observabilidade

- [ ] `AnswerRun` contém todos os estágios e versões.
- [ ] Uma execução selecionada pode ser reconstruída.
- [ ] Logs são estruturados e correlacionados.
- [ ] Traces não incluem texto integral.
- [ ] Métricas não possuem labels de alta cardinalidade com conteúdo.
- [ ] Anonimização possui testes com PII em português.
- [ ] Expiração em 90 dias é automática e testada.
- [ ] Consulta comum não pode aparecer como item colaborativo.

Critérios: AC-15, AC-16, AC-17.

## 18. Benchmark

- [ ] Há casos de todos os tipos especificados.
- [ ] Evidências esperadas são verificáveis.
- [ ] Métricas de recuperação e resposta são separadas.
- [ ] Abstenção é medida.
- [ ] Filtros positivos e negativos são medidos.
- [ ] Comparações entre configurações preservam resultados anteriores.
- [ ] O benchmark registra versões.
- [ ] Avaliação por LLM não é a única fonte de verdade.

Critério: AC-19.

## 19. Docker e operação

- [ ] `docker compose up` inicia ambiente funcional.
- [ ] Health checks convergem.
- [ ] Reinício preserva banco e arquivos.
- [ ] Não há segredo hardcoded.
- [ ] Proxy impede acesso indevido a arquivos internos.
- [ ] Backup inclui banco e artefatos consistentes.
- [ ] Restauração foi testada em dados não sensíveis.
- [ ] O ambiente está restrito a rede privada/local.

Critério: AC-20.

## 20. Matriz resumida de bloqueadores

Reprovar imediatamente se houver:

- citação inventada ou apontando para trecho incorreto;
- resposta dissertativa sem verificação;
- uso silencioso de conhecimento externo;
- modo quote com síntese;
- bypass de exclusão de obra;
- segredo ou malware conhecido em dependências;
- SQL interpolado com entrada do usuário;
- exposição pública sem autenticação;
- stack trace ou credencial em resposta/log;
- perda da distinção entre edições;
- summary usado como evidência primária;
- testes críticos falsamente declarados como executados.

## 21. Formato do parecer

```text
Resultado: aprovado | aprovado com ressalvas | reprovado

Escopo revisado:
- commit/branch:
- ambiente:
- versões:

Evidências executadas:
- comando:
- resultado:

Critérios:
- AC-01: passa | falha | evidência insuficiente
...
- AC-20: passa | falha | evidência insuficiente

Achados:
- severidade:
- requisito:
- evidência:
- impacto:
- correção necessária:

Riscos residuais:
- ...

Conclusão:
- ...
```
