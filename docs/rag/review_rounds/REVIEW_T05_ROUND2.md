# Segunda revisão T05

Data: 2026-08-29
Referências: `docs/rag/REVIEW_T05.md`,
`docs/rag/REVIEW_RESPONSE_T05.md`
Resultado: **correções obrigatórias**

As correções eliminaram o PDF branco e o erro de Unicode. T5-07, T5-08 e
T5-09 também foram confirmados nos caminhos testados. T05, porém, ainda não
está aprovada: o e2e do motor real falha quando habilitado, e a validação de
proveniência é contornada por `--dry-run` e pela reingestão de uma edição
existente.

## Estado dos achados anteriores

| Item | Estado |
|---|---|
| T5-01 | parcial — páginas originais são reutilizadas; faltam provas de alinhamento e comparação real dos pixels |
| T5-02 | corrigido — round-trip Unicode confirmado |
| T5-03 | parcial — metadados bibliográficos cobertos; contrato OCR não é comparado na reingestão |
| T5-04 | parcial — sidecar foi criado, mas há caminhos que não o validam |
| T5-05 | não corrigido — o teste opcional falha e não prova reconhecimento |
| T5-06 | parcial — spans são divididos, mas `original_text` deixa de preservar o original |
| T5-07 | corrigido |
| T5-08 | corrigido nos caminhos testados |
| T5-09 | corrigido |
| T5-10 | parcial — JSON e remoção do traceback do console funcionam; stream e debug log contradizem o contrato |

## Bloqueadores

### R5-01 — O e2e de OCR real falha quando habilitado

Comando executado:

```text
RAG_OCR_E2E=1 uv run pytest \
  tests/unit/test_ocr.py::TestOcrPdf::test_real_engine_scan_to_ingest_pipeline -q
```

Resultado: **falha**.

`DoclingOcrEngine(engine="rapidocr")` seleciona o backend ONNX do RapidOCR,
mas `onnxruntime` não está instalado:

```text
ImportError: onnxruntime is not installed.
rag.domain.errors.IngestionError: Falha no OCR do documento.
```

Isso contradiz `REVIEW_RESPONSE_T05.md`, que descreve o motor como “já
disponível localmente”, e `EVIDENCE.md`, que apresenta esse teste como prova
do motor real.

Correção esperada:

- escolher uma configuração real que funcione no ambiente suportado; ou
- solicitar aprovação explícita antes de declarar e fixar qualquer dependência
  adicional necessária;
- executar o teste habilitado e registrar o resultado real;
- documentar os pré-requisitos por engine.

Não adicionar `onnxruntime` ou outra dependência silenciosamente.

### R5-02 — `--dry-run` não valida a proveniência OCR

Arquivo: `backend/src/rag/application/ingest.py`

Para `pdf_scan`, o sidecar só é carregado em `_store_ocr_derivative()`, depois
da extração e depois do retorno antecipado de `dry_run`. Assim, um derivado
sem sidecar, adulterado ou pertencente a outro livro pode passar no dry-run.

Isso viola o contrato explícito de que `--dry-run` valida os inputs sem
persistir. A validação de proveniência deve ocorrer antes da extração e antes
do branch de dry-run.

Testes necessários:

- dry-run rejeita sidecar ausente;
- dry-run rejeita `input_sha256` de outro original;
- dry-run rejeita `output_sha256` adulterado;
- dry-run rejeita página/proveniência incoerente;
- nada é persistido em todos esses casos.

### R5-03 — Reingestão existente também contorna a proveniência OCR

Arquivo: `backend/src/rag/application/ingest.py`

Quando `source_sha256` já existe, `ingest()` retorna antes de
`_store_ocr_derivative()`. `_assert_coherent()` não compara:

- hash do `ocr_artifact` informado;
- sidecar;
- hash de entrada do sidecar;
- engine/versão;
- derivado já associado à edição.

Portanto, após uma ingestão válida, a mesma varredura pode ser reingerida com
outro derivado, sidecar ausente/adulterado ou artefato de outro livro e ainda
receber sucesso idempotente.

Validar o contrato OCR antes do retorno de deduplicação e provar que o
derivado informado é equivalente ao já registrado. Adicionar testes para
todos esses casos.

### R5-04 — PDF e sidecar de proveniência não são publicados como uma operação segura

Arquivos:

- `backend/src/rag/adapters/ocr_adapter.py`
- `backend/src/rag/adapters/pdf_writer.py`

O PDF é publicado primeiro por `os.replace()`. Depois,
`_write_provenance()` usa `Path.write_text()` diretamente. Se essa escrita
falhar ou for interrompida:

- o PDF permanece publicado sem sidecar;
- o sidecar pode ficar truncado;
- uma execução anterior válida pode ser substituída por um par incompleto.

Isso contraria “o CLI não publica automaticamente arquivos parcialmente
processados”.

Correção esperada:

- sidecar com escrita temporária, `fsync` e rename;
- limpeza/rollback seguro se a publicação do par falhar;
- não destruir um par válido anterior antes de o novo par estar pronto;
- testes de falha na escrita, no rename e no fsync de cada arquivo.

## Correções importantes

### R5-05 — O teste chamado de e2e pode passar sem reconhecer texto

Arquivos:

- `backend/tests/fixtures/builders.py`
- `backend/tests/unit/test_ocr.py`

`make_scanned_pdf()` desenha apenas um retângulo vetorial; não contém imagem
com texto para OCR. O teste opcional não verifica `report.lines > 0` nem
confere texto reconhecido no derivado. Apesar do nome
`scan_to_ingest_pipeline`, ele também não executa `rag ingest`.

Mesmo após corrigir R5-01, o teste pode passar com zero reconhecimento.

Usar uma fixture raster image-only com frase legível em português e provar:

1. o original não possui text layer;
2. o motor reconhece a frase;
3. o derivado preserva a imagem e expõe o texto;
4. a ingestão persiste esse texto e a proveniência correta.

### R5-06 — A evidência de preservação visual compara apenas extremos

`_render_extrema()` retorna somente `(mínimo, máximo)` em escala de cinza.
Duas imagens diferentes podem compartilhar `(20, 255)`. Portanto,
`test_text_layer_preserves_visual_content_and_is_searchable` não compara os
pixels, embora a resposta e `EVIDENCE.md` afirmem isso.

Comparar as imagens completas, por exemplo pela ausência de diferença pixel a
pixel, e incluir página rotacionada. Validar também dimensões do `OcrPage`
contra a página original.

### R5-07 — A camada de texto não possui informação suficiente para alinhamento exato

Arquivo: `backend/src/rag/adapters/pdf_writer.py`

`OcrLine` guarda `x`, `y` e `height`, mas não a largura/bounding box completa,
rotação ou índice físico. O writer:

- não verifica largura/altura informadas pelo motor;
- não ajusta a largura do texto ao bounding box;
- não trata explicitamente páginas rotacionadas;
- associa páginas apenas pela posição na lista.

A imagem é preservada, mas isso ainda não prova que seleção/destaque ficará
sobre o texto reconhecido. Expandir o contrato geométrico e adicionar testes
com renderização/seleção em dimensões e rotações distintas.

### R5-08 — A versão registrada não é a versão real do engine

Arquivo: `backend/src/rag/adapters/ocr_adapter.py`

`DoclingOcrEngine.version` retorna apenas `docling-2.123.1`. Assim:

- RapidOCR não registra sua própria versão/modelos;
- Tesseract não registra sua versão;
- `auto` não registra qual engine foi efetivamente escolhido;
- parâmetros e artefatos de modelo não são identificados.

`rapidocr:docling-2.123.1` não é “engine/versão reais”, como afirma a resposta.
Registrar separadamente adapter, engine resolvido, versão do engine, modelos
ou hashes relevantes e parâmetros.

### R5-09 — `pypdfium2` virou dependência direta de runtime sem ser declarada

`pdf_writer.py` agora importa `pypdfium2` diretamente, mas o pacote aparece
somente como dependência transitiva de `docling-slim` no lockfile. Código de
produção não deve depender de um detalhe transitivo que pode desaparecer em
uma atualização.

Se a implementação continuar usando essa API, solicitar aprovação e declarar
uma versão direta e pinada em `pyproject.toml`, atualizando e auditando o
lockfile. A presença transitiva atual não substitui essa declaração.

### R5-10 — O contrato de logs ainda não corresponde ao comportamento

Arquivo: `backend/src/rag/cli/main.py`

Confirmação independente:

```text
stdout= '{"event": "probe", ...}\n'
stderr= ''
```

Sem `logger_factory` explícita, os logs estruturados vão para stdout, embora o
módulo e `EVIDENCE.md` afirmem stderr.

Além disso, `RAG_DEBUG_LOG` recebe traceback e mensagem completos, sem
redação nem garantia de permissões restritas; a falha ao abrir esse arquivo
pode mascarar a exceção original dentro do processor. Configuração de debug
não suspende as regras de redigir tokens, credenciais e dados pessoais.

Configurar stderr explicitamente e tornar o sink de debug redigido, restrito e
fail-safe. Testar streams separados, segredos e caminho de debug inválido.

### R5-11 — `original_text` é perdido ao dividir múltiplos spans

Arquivo: `backend/src/rag/adapters/docling_adapter.py`

Para mais de um `prov`, `_spans_of()` retorna `segment` tanto como texto
normalizado quanto como original, ignorando `item.orig`. A correção de página
foi feita, mas a exigência de preservar texto original suficiente para
citação deixa de valer nesse caminho.

Definir o mapeamento correto entre `charspan`, `item.text` e `item.orig`, ou
registrar uma representação que preserve ambos sem alegar equivalência.

## Gates executados

| Comando | Resultado |
|---|---|
| `make lock` | OK |
| `make lint` | OK |
| `make format-check` | OK |
| `make typecheck` | OK |
| `make test` | 223 backend passed, 2 skipped; 1 frontend passed |
| `make test-integration` | 84 passed |
| `make audit` | nenhuma vulnerabilidade conhecida |
| `make security-scan` | nenhum IOC bloqueado |
| e2e com `RAG_OCR_E2E=1` | **FALHOU: `onnxruntime` ausente** |

## Condição para aprovação

Corrigir R5-01 a R5-04. Tratar R5-05 a R5-11 ou registrar justificativa
técnica verificável para qualquer adiamento. Atualizar as afirmações de
`REVIEW_RESPONSE_T05.md`, `EVIDENCE.md` e `NOTES.md` para refletirem os
resultados efetivamente reproduzidos.

T06 permanece bloqueada.
