"""Add company_embeddings table for semantic purpose search.

Stores 768-dim L2-normalized embeddings per company × embedding_type:
  'purpose_full'  — full raw purpose text
  'purpose_clean' — boilerplate-stripped purpose text

Revision ID: 0083
Revises: 0082_add_vat_fields
Create Date: 2026-05-21
"""

from alembic import op

revision = "0083"
down_revision = "0082_add_vat_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
        CREATE TABLE company_embeddings (
            id            SERIAL PRIMARY KEY,
            company_id    INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            embedding_type VARCHAR(32) NOT NULL,
            embedding     vector(768) NOT NULL,
            lang          VARCHAR(8),
            computed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT company_embeddings_company_type_key UNIQUE (company_id, embedding_type)
        )
    """)

    # Partial IVFFlat index per embedding_type.
    # lists = sqrt(700_000) ≈ 836 — created empty, becomes useful after batch job.
    op.execute("""
        CREATE INDEX company_emb_full_ivfflat_idx
        ON company_embeddings USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 1000)
        WHERE embedding_type = 'purpose_full'
    """)
    op.execute("""
        CREATE INDEX company_emb_clean_ivfflat_idx
        ON company_embeddings USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 1000)
        WHERE embedding_type = 'purpose_clean'
    """)

    # B-tree index for fast company_id lookups (upsert / delete cascade checks).
    op.execute("""
        CREATE INDEX company_emb_company_id_idx
        ON company_embeddings (company_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS company_embeddings")
