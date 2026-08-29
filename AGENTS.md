# Orientações para o agente implementador

## Papel

Você implementará a fase 1 do RAG de livros. Outro agente escreveu a especificação e fará uma revisão independente ao final. Não altere requisitos para facilitar a implementação nem declare trabalho concluído sem evidências reproduzíveis.

## Fontes obrigatórias

Leia estes documentos integralmente antes de escrever código:

1. `docs/rag/SPECIFICATION.md` — requisitos e critérios `AC-01` a `AC-20`;
2. `docs/rag/NOTES.md` — decisões, premissas, recusas e itens adiados;
3. `docs/rag/TASKS.md` — ordem, dependências e definição de pronto;
4. `docs/rag/REVIEW_CHECKLIST.md` — como o resultado será julgado.

Em caso de conflito, a especificação prevalece. Se ainda houver ambiguidade material, pare e pergunte ao usuário. Não escolha silenciosamente.

## Escopo

- Implemente somente a fase 1.
- Não implemente plataforma colaborativa, autenticação própria, multi-tenancy, RAG multimodal, banco de grafos, Kubernetes ou memória persistente.
- Mantenha o ambiente privado/local.
- Não exponha o sistema publicamente.
- Não substitua PostgreSQL + pgvector sem evidência, proposta e aprovação.
- Não use LangChain como núcleo. Tipos de framework ou SDK não podem atravessar o domínio.

## Método de trabalho

1. Execute as tarefas `T01` a `T20` respeitando dependências.
2. Antes de cada tarefa, identifique requisitos e critérios `AC-*` relacionados.
3. Implemente o menor incremento vertical verificável.
4. Adicione testes positivos, negativos e de falha no mesmo incremento.
5. Execute lint, formatação, type checking e testes relevantes.
6. Registre comandos, resultados e limitações.
7. Atualize uma matriz `AC-01` a `AC-20` com links para testes e evidências.
8. Só marque uma tarefa concluída quando sua definição de pronto estiver satisfeita.

Não crie commits, faça push ou abra PR sem solicitação explícita do usuário.

## Alterações de arquitetura

Quando uma decisão parecer inviável:

1. registre a decisão atual;
2. apresente evidência concreta do problema;
3. compare alternativas e impactos;
4. proponha migração e rollback;
5. aguarde aprovação antes de implementar o desvio.

Não edite critérios de aceitação ou o checklist de revisão para fazer o código parecer conforme.

## Dependências

- Peça aprovação explícita antes de adicionar qualquer dependência.
- Use apenas registros oficiais e versões específicas pinadas.
- Mantenha e valide lockfiles.
- Não use versões sem limite, URLs, forks pessoais ou scripts não revisados.
- É proibido usar `plain-crypto-js`, Axios 1.14.1 ou Axios 0.30.4.
- Se qualquer indicador bloqueado já existir, pare e reporte um incidente crítico.

## Regras de implementação

- Backend Python tipado; requests e responses Pydantic.
- Domínio independente de FastAPI, ORM, Docling e SDKs de modelos.
- SQL sempre parametrizado.
- Segredos somente por ambiente ou secret files; nunca em código, logs ou exemplos.
- Erros externos são tipados e sanitizados.
- Falhas de modelos devem falhar fechadas; nunca gere resposta sem evidências como fallback.
- `quote` não chama o gerador nem produz paráfrase.
- `dissertative` sempre executa verificação antes de retornar sucesso.
- Resumos ajudam a recuperar, mas nunca são evidência primária.
- Exclusões de obras valem em todos os estágios da recuperação.
- Toda resposta registra versões, parâmetros, rankings e evidências.

## Testes e evidências

- Prefira testes determinísticos e fixtures pequenas legalmente utilizáveis.
- Teste contratos HTTP com servidores simulados, além de integrações reais configuráveis.
- Não esconda falhas com skips, retries indiscriminados ou assertions fracas.
- Mocks devem validar contratos; não podem substituir todos os caminhos de integração.
- Inclua testes de timeout, payload inválido, filtros, abstenção, citações falsas, edições distintas e vazamento entre sessões.
- Demonstração visual não substitui teste automatizado.

## Segurança e privacidade

- Valide body, query, path, arquivos e metadados.
- Proteja armazenamento contra path traversal e gravação parcial.
- Aplique rate limiting, CORS explícito e security headers.
- Não exponha stack traces, SQL, caminhos locais ou conteúdo integral dos livros.
- Redija tokens, credenciais e dados pessoais em logs.
- Não coloque texto integral em traces ou labels de métricas.
- Implemente anonimização e expiração verificável em 90 dias.

## Entrega ao revisor

Forneça:

- matriz completa de `AC-01` a `AC-20`;
- comandos e resultados de lint, typecheck e todos os níveis de teste;
- relatório do benchmark;
- inventário de dependências e versões;
- lista de desvios aprovados e riscos residuais;
- instruções para iniciar, ingerir fixtures, consultar e reproduzir evidências;
- commit ou diff exato submetido à revisão.

O revisor poderá reprovar a implementação por evidência insuficiente mesmo quando o comportamento aparente estiver correto.
