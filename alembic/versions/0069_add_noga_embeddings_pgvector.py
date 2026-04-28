"""Add noga_embeddings table with pgvector for language-aware NOGA classification.

Revision ID: 0069
Revises: 0068
Create Date: 2026-04-27
"""

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE noga_embeddings (
            id        SERIAL PRIMARY KEY,
            noga_code VARCHAR(10)  NOT NULL,
            lang      VARCHAR(4)   NOT NULL,
            embedding vector(768)  NOT NULL,
            UNIQUE (noga_code, lang)
        )
    """)

    # IVFFlat approximate nearest-neighbour index.
    # ~8000 rows (2000 codes × 4 langs) → lists = sqrt(8000) ≈ 90.
    # Index must be built after rows are inserted; the loader script runs
    # after migration so this is fine.
    op.execute("""
        CREATE INDEX noga_embeddings_ivfflat_idx
        ON noga_embeddings USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 90)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS noga_embeddings")
    # Intentionally leave the vector extension installed — removing it could
    # break other objects if the extension is reused later.
