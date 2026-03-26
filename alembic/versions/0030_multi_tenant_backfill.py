"""Bootstrap multi-tenancy: create legacy org, assign existing data.

For single-tenant deployments being upgraded:
1. Creates a "Legacy" organization if none exists.
2. Assigns all users without an org to the legacy org (first user gets owner role).
3. Backfills org_company_state from existing companies (workflow/contact/google fields).
4. Backfills user_company_state from existing companies (claude fields → legacy owner).
5. Assigns existing notes to legacy org + legacy owner (if user_id is NULL).
6. Assigns existing job_runs to legacy org.

Revision ID: 0030
Revises: 0029
Create Date: 2026-03-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Create legacy org if there are no orgs yet ──────────────────────────
    existing_orgs = conn.execute(sa.text("SELECT id FROM organizations LIMIT 1")).fetchone()
    if existing_orgs is None:
        conn.execute(sa.text(
            "INSERT INTO organizations (name, slug, tier, created_at) "
            "VALUES ('Legacy', 'legacy', 'free', NOW())"
        ))

    legacy_org = conn.execute(sa.text("SELECT id FROM organizations ORDER BY id LIMIT 1")).fetchone()
    legacy_org_id = legacy_org[0]

    # ── 2. Assign users without an org to the legacy org ──────────────────────
    conn.execute(sa.text(
        "UPDATE users SET org_id = :org_id WHERE org_id IS NULL"
    ), {"org_id": legacy_org_id})

    # First user becomes owner
    first_user = conn.execute(sa.text("SELECT id FROM users ORDER BY id LIMIT 1")).fetchone()
    if first_user:
        legacy_owner_id = first_user[0]
        conn.execute(sa.text(
            "UPDATE users SET org_role = 'owner' WHERE id = :uid"
        ), {"uid": legacy_owner_id})
    else:
        legacy_owner_id = None

    # ── 3. Backfill org_company_state from companies (workflow + google fields) ─
    # Only create rows where there is actual overlay data to migrate.
    conn.execute(sa.text("""
        INSERT INTO org_company_state (
            org_id, company_id,
            tags, review_status, proposal_status,
            contact_name, contact_email, contact_phone,
            website_url, website_match_score, google_search_results_raw,
            website_checked_at, social_media_only,
            created_at, updated_at
        )
        SELECT
            :org_id, id,
            tags, review_status, proposal_status,
            contact_name, contact_email, contact_phone,
            website_url, website_match_score, google_search_results_raw,
            website_checked_at, social_media_only,
            COALESCE(updated_at, NOW()), COALESCE(updated_at, NOW())
        FROM companies
        WHERE (
            tags IS NOT NULL OR review_status IS NOT NULL OR proposal_status IS NOT NULL
            OR contact_name IS NOT NULL OR contact_email IS NOT NULL OR contact_phone IS NOT NULL
            OR website_url IS NOT NULL OR website_match_score IS NOT NULL
            OR google_search_results_raw IS NOT NULL OR website_checked_at IS NOT NULL
        )
        ON CONFLICT DO NOTHING
    """), {"org_id": legacy_org_id})

    # ── 4. Backfill user_company_state from companies (claude fields) ──────────
    if legacy_owner_id is not None:
        conn.execute(sa.text("""
            INSERT INTO user_company_state (
                user_id, company_id, org_id,
                claude_score, claude_category, claude_freeform, claude_scored_at,
                created_at, updated_at
            )
            SELECT
                :user_id, id, :org_id,
                claude_score, claude_category, claude_freeform, claude_scored_at,
                COALESCE(updated_at, NOW()), COALESCE(updated_at, NOW())
            FROM companies
            WHERE claude_score IS NOT NULL OR claude_category IS NOT NULL OR claude_freeform IS NOT NULL
            ON CONFLICT DO NOTHING
        """), {"user_id": legacy_owner_id, "org_id": legacy_org_id})

    # ── 5. Assign existing notes to legacy org + owner ─────────────────────────
    conn.execute(sa.text(
        "UPDATE notes SET org_id = :org_id WHERE org_id IS NULL"
    ), {"org_id": legacy_org_id})
    if legacy_owner_id is not None:
        conn.execute(sa.text(
            "UPDATE notes SET user_id = :user_id WHERE user_id IS NULL"
        ), {"user_id": legacy_owner_id})

    # ── 6. Assign existing job_runs to legacy org ──────────────────────────────
    conn.execute(sa.text(
        "UPDATE job_runs SET org_id = :org_id WHERE org_id IS NULL"
    ), {"org_id": legacy_org_id})


def downgrade() -> None:
    # Downgrade is intentionally a no-op: we cannot safely determine which data
    # was pre-existing vs. migrated by this script. Run downgrade of 0029/0028
    # to drop the columns entirely if rolling back the full migration set.
    pass
