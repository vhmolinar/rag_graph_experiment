"""Capacidades fixas do schema físico desta revisão (migration 0001).

RR05: a coluna `passages.embedding` é `vector(1024)`. Uma EmbeddingVersion com
dimensão diferente nunca poderia ser persistida — o registro é rejeitado no
cadastro, antes de qualquer processamento de documento. Se uma futura revisão
ampliar a capacidade, a constante e a migration devem mudar juntas.
"""

EMBEDDING_COLUMN_DIMENSIONS = 1024
