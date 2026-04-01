"""Tier stored as integer; rename org role 'member' -> 'contributor'.

Revision ID: 0041
Revises: 0040
Create Date: 2026-04-01

Changes
-------
* organizations.tier  VARCHAR → INTEGER (0=free,1=simple,2=explorer,
                                         3=researcher,4=strategist,5=custom)
* org_members.role    'member' → 'contributor'
* users.org_role      'member' → 'contributor'
"""

from alembic import op
import sqlalchemy as sa

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

_TIER_TO_INT = {
    "free": 0,
    "simple": 1,
    "explorer": 2,
    "researcher": 3,
    "strategist": 4,
    "custom": 5,
}


def upgrade() -> None:
    # ── organizations.tier: VARCHAR → INTEGER ──────────────────────────────
    # 1. Add temporary integer column
    op.add_column("organizations", sa.Column("tier_int", sa.Integer(), nullable=True))

    # 2. Populate from the current string values
    conn = op.get_bind()
    for name, rank in _TIER_TO_INT.items():
        conn.execute(
            sa.text("UPDATE organizations SET tier_int = :rank WHERE tier = :name"),
            {"rank": rank, "name": name},
        )
    # Anything unrecognised defaults to 0 (free)
    conn.execute(sa.text("UPDATE organizations SET tier_int = 0 WHERE tier_int IS NULL"))

    # 3. Drop old VARCHAR column, rename temp column
    op.drop_column("organizations", "tier")
    op.alter_column("organizations", "tier_int", new_column_name="tier",
                    nullable=False, server_default="0")

    # ── org_members.role: 'member' → 'contributor' ─────────────────────────
    conn.execute(
        sa.text("UPDATE org_members SET role = 'contributor' WHERE role = 'member'")
    )

    # ── users.org_role: 'member' → 'contributor' ───────────────────────────
    conn.execute(
        sa.text("UPDATE users SET org_role = 'contributor' WHERE org_role = 'member'")
    )


def downgrade() -> None:
    # ── users.org_role and org_members.role: reverse ───────────────────────
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE users SET org_role = 'member' WHERE org_role = 'contributor'")
    )
    conn.execute(
        sa.text("UPDATE org_members SET role = 'member' WHERE role = 'contributor'")
    )

    # ── organizations.tier: INTEGER → VARCHAR ──────────────────────────────
    _INT_TO_TIER = {v: k for k, v in _TIER_TO_INT.items()}
    op.add_column("organizations", sa.Column("tier_str", sa.String(32), nullable=True))
    for rank, name in _INT_TO_TIER.items():
        conn.execute(
            sa.text("UPDATE organizations SET tier_str = :name WHERE tier = :rank"),
            {"name": name, "rank": rank},
        )
    conn.execute(sa.text("UPDATE organizations SET tier_str = 'free' WHERE tier_str IS NULL"))
    op.drop_column("organizations", "tier")
    op.alter_column("organizations", "tier_str", new_column_name="tier",
                    nullable=False, server_default="free")
