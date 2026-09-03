# Remediação R01 — Fidelidade literal de EPUB

**Status:** concluída  
**Data:** 2026-09-03  
**ACs afetados:** AC-03, AC-08

## Problema identificado

Para EPUB, quando um chunk cobria apenas parte das sentenças de um bloco, o `original_text` 
usava o texto normalizado (sentenças unidas por espaço) em vez do texto original do bloco 
inteiro. Isso violava a fidelidade literal (AC-03) porque:

1. O texto normalizado perdia formatação significativa (quebras de linha, espaços múltiplos)
2. Para EPUB, não há offsets de página para fatiar com precisão
3. A citação literal deve preservar o texto original, mesmo que redundante

## Solução implementada

### Modificação em `backend/src/rag/domain/chunking.py`

A função `_original_text_of()` foi ajustada para distinguir PDF de EPUB:

- **PDF (com páginas):** quando um bloco está parcialmente coberto, usa o texto normalizado — 
  os offsets de página garantem precisão na citação (comportamento anterior, preservado).

- **EPUB (sem páginas):** sempre usa o `block_original_text` inteiro dos blocos envolvidos, 
  mesmo quando parcialmente cobertos. Isso garante fidelidade literal ao original (AC-03), 
  mesmo que redundante — para EPUB, não há offsets de página para fatiar com precisão, e o 
  texto normalizado perderia formatação significativa.

### Testes adicionados

Novo arquivo `tests/unit/test_epub_fidelity.py` com 4 testes:

1. `test_epub_chunk_uses_block_original_text_even_when_partial` — verifica que EPUB usa o 
   bloco inteiro mesmo com cobertura parcial
2. `test_epub_chunk_preserves_original_whitespace_and_formatting` — verifica que formatação 
   original (espaços múltiplos, quebras de linha) é preservada
3. `test_epub_chunk_with_multiple_blocks_preserves_all_originals` — verifica que múltiplos 
   blocos são preservados na ordem
4. `test_pdf_chunk_with_partial_block_uses_normalized_text` — verifica que PDF mantém o 
   comportamento anterior (usa texto normalizado para blocos parciais)

### Teste atualizado

`tests/unit/test_chunking.py::TestOriginalText::test_split_aligned_block_has_exact_text_per_child` 
foi atualizado para refletir o novo comportamento correto: para EPUB, mesmo quando o bloco 
está alinhado (text == original_text), os filhos recebem o bloco inteiro como original_text 
para garantir fidelidade literal.

## Evidências

### Comandos executados

```bash
# Testes unitários de fidelidade EPUB
uv run pytest tests/unit/test_epub_fidelity.py -v
# Resultado: 4 passed

# Testes unitários de chunking
uv run pytest tests/unit/test_chunking.py -v
# Resultado: 21 passed (incluindo teste atualizado)

# Todos os testes unitários
uv run pytest tests/unit/ -v
# Resultado: 615 passed, 3 skipped

# Lint
uv run ruff check .
# Resultado: All checks passed!

# Type check
uv run mypy src/rag/
# Resultado: Success: no issues found in 75 source files
```

### Limitações

- Testes de integração requerem Docker (não disponível neste ambiente)
- O comportamento de EPUB com markup HTML complexo (listas aninhadas, blockquotes, etc.) 
  não foi testado — a fixture `make_epub` gera apenas estrutura simples (h1 + p)

## Impacto

- **AC-03 (passagem citada abre edição, página e trecho corretos):** melhorado para EPUB — 
  o texto citável agora preserva o original, não o normalizado
- **AC-08 (modo quote não contém texto sintetizado):** reforçado — a citação literal de EPUB 
  é agora genuinamente literal

## Riscos residuais

- EPUB com estrutura HTML complexa pode ter formatação perdida na extração Docling (fora do 
  escopo desta remediação — depende do adapter Docling)
- Frontend (T17) precisará de estratégia de navegação para EPUB sem paginação estável 
  (adiado para T17)
