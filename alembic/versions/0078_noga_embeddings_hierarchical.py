"""Extend noga_embeddings for hierarchical level-by-level classification.

Adds level_no (1-5), parent_code, and ann_type ('includes'|'excludes') columns.
Swaps the single global IVFFlat index for 5 per-level partial indexes restricted
to ann_type='includes' (the only rows used in ANN search).

Revision ID: 0078
Revises: 0077_add_sogc_indexes
Create Date: 2026-05-14
"""

from alembic import op

revision = "0078"
down_revision = "0077_add_sogc_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add nullable first so we can backfill before setting NOT NULL.
    op.execute("ALTER TABLE noga_embeddings ADD COLUMN level_no    SMALLINT")
    op.execute("ALTER TABLE noga_embeddings ADD COLUMN parent_code VARCHAR(10)")
    op.execute("ALTER TABLE noga_embeddings ADD COLUMN ann_type    VARCHAR(16)")

    # Backfill existing rows: all are level-5 INCLUDES embeddings.
    # Level-5 NOGA codes are 6-digit numeric (e.g. "263001").
    # Their parent is the 4-digit prefix (e.g. "2630").
    op.execute("""
        UPDATE noga_embeddings
        SET    level_no    = 5,
               parent_code = LEFT(noga_code, 4),
               ann_type    = 'includes'
        WHERE  noga_code ~ '^[0-9]{6}$'
    """)

    op.execute("ALTER TABLE noga_embeddings ALTER COLUMN level_no  SET NOT NULL")
    op.execute("ALTER TABLE noga_embeddings ALTER COLUMN ann_type  SET NOT NULL")

    # Swap unique constraint to include ann_type (each code/lang pair now has
    # up to 2 rows: one 'includes' and one 'excludes').
    op.execute("""
        ALTER TABLE noga_embeddings
        DROP CONSTRAINT IF EXISTS noga_embeddings_noga_code_lang_key
    """)
    op.execute("""
        ALTER TABLE noga_embeddings
        ADD CONSTRAINT noga_embeddings_code_lang_anntype_key
        UNIQUE (noga_code, lang, ann_type)
    """)

    # Drop the old single global IVFFlat (wrong sizing, no level filter).
    op.execute("DROP INDEX IF EXISTS noga_embeddings_ivfflat_idx")

    # Per-level partial IVFFlat indexes scoped to INCLUDES rows only.
    # lists = sqrt(N) where N = codes_at_level × 4_langs.
    # Indexes are created empty here; they become useful after the
    # build_noga_embeddings job populates all 5 levels (same precedent as 0069).
    op.execute("""
        CREATE INDEX noga_emb_inc_l1_idx
        ON noga_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)
        WHERE level_no = 1 AND ann_type = 'includes'
    """)
    op.execute("""
        CREATE INDEX noga_emb_inc_l2_idx
        ON noga_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)
        WHERE level_no = 2 AND ann_type = 'includes'
    """)
    op.execute("""
        CREATE INDEX noga_emb_inc_l3_idx
        ON noga_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 34)
        WHERE level_no = 3 AND ann_type = 'includes'
    """)
    op.execute("""
        CREATE INDEX noga_emb_inc_l4_idx
        ON noga_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 51)
        WHERE level_no = 4 AND ann_type = 'includes'
    """)
    op.execute("""
        CREATE INDEX noga_emb_inc_l5_idx
        ON noga_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 57)
        WHERE level_no = 5 AND ann_type = 'includes'
    """)

    # B-tree index for fast point-lookup of EXCLUDES rows by code+lang.
    op.execute("""
        CREATE INDEX noga_emb_excl_lookup_idx
        ON noga_embeddings (noga_code, lang)
        WHERE ann_type = 'excludes'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS noga_emb_excl_lookup_idx")
    op.execute("DROP INDEX IF EXISTS noga_emb_inc_l5_idx")
    op.execute("DROP INDEX IF EXISTS noga_emb_inc_l4_idx")
    op.execute("DROP INDEX IF EXISTS noga_emb_inc_l3_idx")
    op.execute("DROP INDEX IF EXISTS noga_emb_inc_l2_idx")
    op.execute("DROP INDEX IF EXISTS noga_emb_inc_l1_idx")

    op.execute("""
        ALTER TABLE noga_embeddings
        DROP CONSTRAINT IF EXISTS noga_embeddings_code_lang_anntype_key
    """)
    op.execute("""
        ALTER TABLE noga_embeddings
        ADD CONSTRAINT noga_embeddings_noga_code_lang_key UNIQUE (noga_code, lang)
    """)

    op.execute("""
        CREATE INDEX noga_embeddings_ivfflat_idx
        ON noga_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 90)
    """)

    op.execute("ALTER TABLE noga_embeddings DROP COLUMN IF EXISTS ann_type")
    op.execute("ALTER TABLE noga_embeddings DROP COLUMN IF EXISTS parent_code")
    op.execute("ALTER TABLE noga_embeddings DROP COLUMN IF EXISTS level_no")
