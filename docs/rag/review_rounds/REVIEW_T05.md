# Revisão T05 — Representação canônica e adapters Docling

Data: 2026-08-29
Resultado: **correções obrigatórias**

T05 ainda não está aprovada. O schema canônico, a ingestão de EPUB/PDF-texto e
os comandos básicos estão bem encaminhados, mas o contrato de OCR não é
cumprido: o “derivado OCR” perde a imagem do documento original. Há também
lacunas na idempotência por metadados e na proveniência do derivado.

## Bloqueadores

### T5-01 — O PDF derivado por OCR não contém a varredura original

Arquivos:

- `backend/src/rag/adapters/pdf_writer.py`
- `backend/src/rag/adapters/ocr_adapter.py`
- `backend/tests/unit/test_ocr.py`

`write_text_layer_pdf()` constrói um PDF novo, com páginas vazias e texto
invisível (`3 Tr`). Nenhum conteúdo ou imagem das páginas do PDF de entrada é
copiado para o resultado. Portanto, o texto não está “sobre a imagem”, ao
contrário do que afirmam o docstring, `EVIDENCE.md` e o contrato de
`NOTES.md` §10.1.5.

Confirmação independente: a renderização de uma página produzida pelo writer
resultou em imagem totalmente branca (`render_extrema (255, 255)`), embora a
extração de texto encontre o conteúdo invisível.

Impactos:

- o PDF.js exibiria páginas brancas para o derivado;
- não é possível conferir texto reconhecido contra a página digitalizada;
- o caminho futuro de destaque de citações de scans fica inviável;
- `test_text_layer_is_extractable_and_searchable` prova apenas extração de
  texto, não preservação visual.

Correção esperada:

- copiar ou mesclar cada página do PDF original e adicionar a camada invisível
  sobre ela;
- preservar quantidade, dimensões, rotação e conteúdo visual das páginas;
- testar com fixture realmente image-only;
- renderizar original e derivado e provar que o conteúdo visual foi preservado;
- provar também que o texto do derivado é pesquisável.

### T5-02 — O writer de OCR falha com caracteres Unicode comuns em livros

Arquivo: `backend/src/rag/adapters/pdf_writer.py`

`_escape()` usa `ch.encode("latin-1")`. Caracteres comuns como travessão (`—`),
aspas tipográficas e caracteres fora de Latin-1 causam `UnicodeEncodeError`.
Isso foi reproduzido com `travessão — teste`.

Correção esperada:

- usar fonte incorporada e codificação Unicode adequada no PDF;
- cobrir ao menos acentos portugueses, `—`, `“”`, símbolos e uma sequência fora
  de Latin-1;
- garantir round-trip pela extração de texto do PDF.

### T5-03 — Reingestão aceita metadados divergentes como se fossem idênticos

Arquivos:

- `backend/src/rag/application/ingest.py`
- `backend/tests/integration/test_ingest.py`

`_assert_coherent()` compara somente `title`, `edition_label` e `isbn`. O mesmo
arquivo pode ser reingerido com autores, título original, editora, ano,
licença, idioma ou `source_type` diferentes e ainda retornar sucesso apontando
para a edição existente.

Isso torna incompleta a evidência de AC-01: o teste atual altera apenas o
título.

Correção esperada:

- definir explicitamente a identidade e os metadados imutáveis da ingestão;
- comparar todos esses campos, incluindo a identidade da obra/autores e o
  contrato OCR quando aplicável;
- adicionar testes parametrizados para cada divergência;
- manter exit code zero apenas para repetição realmente equivalente.

### T5-04 — A proveniência do OCR é declarada, mas não validada

Arquivos:

- `backend/src/rag/application/ingest.py`
- `backend/src/rag/adapters/ocr_adapter.py`

Qualquer PDF informado em `ocr_artifact` é registrado como derivado do scan,
sem prova de que foi produzido daquele original e sem validação de alinhamento
de páginas. Além disso, `generator` é sempre `"docling-ocr"`, perdendo o engine
real selecionado no CLI.

Um artefato de outro livro pode, portanto, ser associado ao original e gerar
citações em páginas incorretas.

Correção esperada:

- `rag ocr` produzir metadados verificáveis contendo ao menos hashes de entrada
  e saída, engine/versão, quantidade de páginas e parâmetros;
- `rag ingest` validar esses metadados e a correspondência de páginas antes de
  criar `DerivedArtifactRef`;
- persistir o engine/versão real;
- testar rejeição de derivado pertencente a outro original e de contagem de
  páginas divergente.

## Correções importantes

### T5-05 — Não existe o e2e de OCR real declarado

`RAG_OCR_E2E` aparece somente em comentários/documentação. Não há teste com
esse gate e `DoclingOcrEngine` não é executado pela suíte. Os testes usam
`StubEngine`; a fixture chamada de scan é uma página PDF vazia, não uma página
com imagem.

Adicionar ao menos um e2e opcional do fluxo:

`scan image-only → rag ocr → rag ingest → páginas extraídas`

O teste deve validar imagem preservada, texto pesquisável, hashes, engine e
alinhamento das páginas. Corrigir as afirmações correspondentes em
`EVIDENCE.md` até que o teste exista.

### T5-06 — Proveniência de itens Docling com múltiplos spans é truncada

Arquivo: `backend/src/rag/adapters/docling_adapter.py`

O adapter usa somente `item.prov[0]`. Se um item possuir múltiplos spans ou
proveniência em mais de uma página, todos os demais são descartados e o texto
inteiro é atribuído à primeira página. Isso contradiz a preservação de página
e offsets exigida por T05.

Tratar os spans de proveniência explicitamente ou falhar fechado quando não for
possível construir mapeamento exato. Adicionar fixture com múltiplos
`ProvenanceItem`.

### T5-07 — Combinações inválidas entre extensão e `source_type` são aceitas

Arquivo: `backend/src/rag/application/ingest.py`

Há validação de `source_type=epub` com extensão não-EPUB, mas não do inverso.
Por exemplo, um `.epub` pode ser declarado `pdf_text`, e um `.epub` pode ser
declarado `pdf_scan` se houver `ocr_artifact`.

Aplicar uma matriz fechada de combinações válidas e cobri-la com testes.

### T5-08 — Warnings não sobrevivem à ingestão e não aparecem em `rag inspect`

Os warnings são impressos durante `rag ingest`, mas não são persistidos.
`rag inspect` não consegue mostrá-los depois. Isso entrega apenas parcialmente
“warnings e relatório de inspeção”.

Persistir o relatório de extração, ou um artefato canônico versionado que o
contenha, e exibi-lo no inspect.

### T5-09 — Conteúdo textual é descartado sem política completa

Arquivo: `backend/src/rag/adapters/docling_adapter.py`

Notas de rodapé e legendas são descartadas apesar de serem texto potencialmente
relevante para livros. Outros `TextItem` com labels não listados desaparecem
sem warning. O escopo “texto” não implica excluir notas textuais.

Definir e registrar a política de labels. Preservar conteúdo textual citável
ou justificar a exclusão; toda perda deve gerar warning e possuir teste.

### T5-10 — Logs inesperados podem incluir stack traces e caminhos

Arquivo: `backend/src/rag/cli/main.py`

`format_exc_info` junto de `log.exception()` renderiza a exceção completa no
console. Isso pode incluir caminhos absolutos e detalhes internos, contrariando
o contrato declarado pelo próprio módulo. O renderer também é key-value, não
JSON.

Emitir erro sanitizado ao console e reservar traceback para destino interno
explicitamente configurado. Adicionar teste com exceção contendo caminho/texto
sensível.

## Ajustes de evidência

Antes da próxima revisão, corrigir `docs/rag/EVIDENCE.md`:

- AC-01 não está concluído enquanto a coerência de metadados for parcial;
- o OCR atual não preserva a imagem da varredura;
- não há teste `RAG_OCR_E2E`;
- “persistência em transação única” vale apenas para o banco: os blobs são
  gravados antes da transação e podem ficar órfãos, ainda que auditáveis;
- o teste de scan usa um PDF-texto criado diretamente, não o resultado de
  `rag ocr`.

## Evidência executada pelo revisor

Todos os gates existentes passam:

| Comando | Resultado |
|---|---|
| `make lock` | OK |
| `make lint` | OK |
| `make format-check` | OK |
| `make typecheck` | OK |
| `make test` | 213 backend passed, 1 skipped; 1 frontend passed |
| `make test-integration` | 71 passed |
| `make audit` | nenhuma vulnerabilidade conhecida |
| `make security-scan` | nenhum IOC bloqueado |

Os gates verdes não detectam T5-01/T5-02 porque os testes atuais verificam
somente extração de texto Latin-1 em um PDF novo, não preservação visual nem
Unicode abrangente.

## Condição para aprovação

Reenviar T05 com T5-01 a T5-04 corrigidos, T5-05 a T5-10 tratados ou
explicitamente justificados, evidência atualizada e todos os gates verdes.
T06 permanece bloqueada até a aprovação desta tarefa.
