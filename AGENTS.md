# Contexto geral para agentes

## Projeto e fontes de verdade

Este repositório implementa a fase 1 de um RAG privado/local para livros em
português. Todo agente — de implementação, revisão, diagnóstico, documentação
ou operação — deve preservar os requisitos aprovados e basear conclusões em
evidências reproduzíveis. Não altere requisitos, critérios de aceitação ou o
checklist de revisão para aparentar conformidade.

Antes de modificar código, infraestrutura, testes ou documentação normativa,
leia integralmente estes documentos:

1. `docs/rag/SPECIFICATION.md` — requisitos e critérios `AC-01` a `AC-20`;
2. `docs/rag/NOTES.md` — decisões, premissas, recusas e itens adiados;
3. `docs/rag/TASKS.md` — ordem, dependências e definição de pronto;
4. `docs/rag/REVIEW_CHECKLIST.md` — como o resultado será julgado.

Em conflito, a especificação prevalece. Se persistir uma ambiguidade material,
pare e peça orientação ao usuário; não escolha silenciosamente.

## Escopo

- Trabalhe somente no escopo da fase 1 aprovado.
- Não implemente plataforma colaborativa, autenticação própria, multi-tenancy, RAG multimodal, banco de grafos, Kubernetes ou memória persistente.
- Mantenha o ambiente privado/local.
- Não exponha o sistema publicamente.
- Não substitua PostgreSQL + pgvector sem evidência, proposta e aprovação.
- Não use LangChain como núcleo. Tipos de framework ou SDK não podem atravessar o domínio.

## Forma de trabalho

1. Localize a tarefa e as dependências relevantes em `TASKS.md` antes de propor ou executar alterações.
2. Identifique os requisitos e critérios `AC-*` afetados.
3. Faça o menor incremento verificável compatível com o pedido.
4. Ao implementar, inclua testes positivos, negativos e de falha no mesmo incremento.
5. Execute lint, formatação, type checking e os testes pertinentes; registre comandos, resultados e limitações reais.
6. Atualize a matriz de evidências `AC-01` a `AC-20` quando a alteração afetar cobertura ou evidências.
7. Só declare uma tarefa concluída se a respectiva definição de pronto estiver satisfeita.

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

## Handoff e revisão

Ao encerrar trabalho que altere o projeto, forneça, conforme aplicável:

- matriz completa de `AC-01` a `AC-20`;
- comandos e resultados de lint, typecheck e todos os níveis de teste;
- relatório do benchmark;
- inventário de dependências e versões;
- lista de desvios aprovados e riscos residuais;
- instruções para iniciar, ingerir fixtures, consultar e reproduzir evidências;
- commit ou diff exato submetido à revisão.

O revisor deve inspecionar o diff e executar as evidências, não apenas confiar
em relatórios. Evidência insuficiente pode reprovar uma alteração mesmo quando
o comportamento aparente estiver correto.
