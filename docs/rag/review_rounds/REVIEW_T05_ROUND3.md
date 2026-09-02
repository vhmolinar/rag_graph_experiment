# Terceira revisão T05

Data: 2026-08-29
Referências: `docs/rag/REVIEW_T05.md`,
`docs/rag/REVIEW_T05_ROUND2.md`,
`docs/rag/REVIEW_RESPONSE_T05.md`
Resultado: **correções obrigatórias**

Os gates regulares e os dois e2e reais passam. R5-01, R5-02, R5-03 e R5-05
foram confirmados corrigidos. R5-04, porém, continua bloqueante: dois
`os.replace()` consecutivos não publicam dois arquivos atomicamente, e o
caminho de falha entre eles deixa um par incoerente.

## Bloqueador

### R6-01 — Falha no segundo rename publica PDF e sidecar de versões diferentes

Arquivo: `backend/src/rag/adapters/pdf_writer.py`

`publish_derivative_pair()` prepara corretamente os dois temporários, mas
depois executa:

```text
os.replace(pdf_tmp, pdf_path)
os.replace(sidecar_tmp, sidecar_path)
```

Se o primeiro rename funcionar e o segundo falhar, o PDF novo já substituiu
o anterior, enquanto o sidecar antigo permanece. Também sobra o temporário
do sidecar.

Reprodução independente sobre um par previamente válido:

```text
OSError second rename failed
pdf b'pdf-v2'
sidecar b'side-v1'
leftovers ['.out.pdf.provenance.json.<id>.tmp',
           'out.pdf',
           'out.pdf.provenance.json']
```

Os testes atuais cobrem falhas de `fsync` durante a preparação, antes de
qualquer rename. Não cobrem falha no primeiro/segundo rename nem nos
`fsync` de diretório posteriores.

Correção esperada:

- definir um protocolo de commit recuperável para o par;
- em falhas capturáveis, restaurar o par anterior e remover temporários;
- após crash/interrupção, permitir identificar sem ambiguidade qual geração
  está comprometida;
- preferencialmente publicar uma única unidade atômica (manifesto autoritativo
  versionado, diretório versionado com troca atômica, ou proveniência embutida
  em um único artefato);
- adicionar testes de falha em cada rename e em cada fsync, com e sem par
  anterior.

Preparar ambos os temporários antes dos renames reduz a janela, mas não torna
o par atômico.

## Correções importantes

### R6-02 — O motor real ainda emite caminhos absolutos fora do structlog

Arquivos:

- `backend/src/rag/adapters/ocr_adapter.py`
- `backend/src/rag/cli/main.py`

O logger próprio do CLI agora escreve JSON em stderr corretamente. Entretanto,
RapidOCR/Torch escrevem diretamente em stderr e não passam pelos processors de
redação.

Reprodução com `DoclingOcrEngine("rapidocr").recognize(...)`:

```text
contains_absolute_user_path True
... Using /Users/.../.venv/.../rapidocr/models/PP-OCRv6_det_small.pth
... Using /Users/.../.venv/.../rapidocr/models/PP-OCRv6_rec_small.pth
```

Isso contradiz a afirmação de que o CLI não emite caminhos absolutos. Configurar
ou capturar os logs dos adapters externos para que obedeçam à política comum.
Adicionar teste de subprocesso com motor real e streams separados.

### R6-03 — `original_text` continua semanticamente incorreto no fallback

Arquivo: `backend/src/rag/adapters/docling_adapter.py`

Quando `len(item.orig) != len(item.text)`, `_spans_of()` grava o segmento de
`item.text` em `original_text`. O comentário reconhece a perda, mas o contrato
do campo continua afirmando que ele contém o texto original. Não há flag nem
warning no `CanonicalDocument`.

Opções aceitáveis:

- falhar fechado quando não for possível mapear o original;
- preservar o original completo com mapeamento explícito;
- representar fidelidade/ausência de original no schema e emitir warning.

Não preencher um campo chamado `original_text` com texto normalizado sem
registrar a perda.

### R6-04 — Geometria declarada ainda não é validada contra a página

Arquivo: `backend/src/rag/adapters/pdf_writer.py`

`OcrPage.width` e `height` continuam sem uso. O writer verifica quantidade e
índice, mas aceita dimensões incompatíveis. Também ignora o retorno de
`FPDFPage_GenerateContent()` e `_fit_width()` retorna silenciosamente se não
conseguir obter os bounds.

Validar as dimensões contra a página com tolerância explícita e falhar fechado
quando geração/ajuste da camada não puder ser confirmada. O alinhamento
pixel-perfect no leitor pode ficar para T17; a coerência geométrica básica do
artefato pertence a T05.

### R6-05 — `engine_version` ainda não identifica engine/modelo reproduzível

Arquivo: `backend/src/rag/adapters/ocr_adapter.py`

O valor agora registra Docling, nome solicitado e backend, o que é uma melhora.
Ainda não registra a versão do RapidOCR, engine resolvido em `auto`, modelos ou
hashes. Hashes de entrada/saída provam identidade do artefato, mas não permitem
reproduzir a extração.

Se esse detalhamento for adiado, a documentação deve chamá-lo de
`adapter_version`/parâmetros parciais e o adiamento precisa ser aprovado pelo
usuário, com tarefa futura explícita. Não declarar R5-08 integralmente
corrigido enquanto o campo for descrito como versão real do engine.

### R6-06 — A documentação mantém afirmações históricas contraditórias

`EVIDENCE.md` ainda contém a seção anterior dizendo que:

- a preservação visual era provada por `_render_extrema`;
- a fixture OCR era apenas um retângulo;
- o generator era `rapidocr:docling-2.123.1`;
- não havia dependência direta de `pypdfium2`.

A seção ROUND2 posterior corrige essas afirmações, mas a matriz de evidência
deve apresentar o estado atual sem duas versões incompatíveis como se ambas
fossem válidas. Atualizar ou marcar explicitamente a seção anterior como
obsoleta.

## Gates executados

| Comando | Resultado |
|---|---|
| `make lock` | OK |
| `make lint` | OK |
| `make format-check` | OK |
| `make typecheck` | OK |
| `make test` | 233 backend passed, 2 skipped; 1 frontend passed |
| `make test-integration` | 90 passed, 1 skipped |
| `make audit` | nenhuma vulnerabilidade conhecida |
| `make security-scan` | nenhum IOC bloqueado |
| dois e2e com `RAG_OCR_E2E=1` | 2 passed |
| falha simulada no segundo rename | **par final incoerente reproduzido** |

## Condição para aprovação

Corrigir R6-01. Tratar R6-02 a R6-06 ou registrar adiamentos explicitamente
aprovados, sem afirmar conformidade ainda não demonstrada. Reexecutar os gates
regulares, os dois e2e reais e os novos testes de crash/falha de publicação.

T06 permanece bloqueada.
