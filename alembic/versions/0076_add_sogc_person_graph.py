"""Add sogc_person_entities, sogc_person_appearances, sogc_auditors, sogc_person_flags.

Revision ID: 0076_add_sogc_person_graph
Revises: 0075_add_job_run_restart_count
Create Date: 2026-05-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0076_add_sogc_person_graph"
down_revision = "0075_add_job_run_restart_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # sogc_person_entities — create first (others reference it)
    op.create_table(
        "sogc_person_entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("normalized_key", sa.String(512), nullable=False),
        sa.Column("lastname", sa.String(256), nullable=True),
        sa.Column("firstname", sa.String(256), nullable=True),
        sa.Column("hometown_municipality", sa.String(256), nullable=True),
        sa.Column("is_foreign", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("nationality", sa.String(128), nullable=True),
        sa.Column("confidence_level", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("appearance_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_company_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("linkedin_url", sa.String(512), nullable=True),
        sa.Column("linkedin_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_into_id", sa.Integer(), nullable=True),
        sa.Column("identity_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    # Self-referential FK added after table creation
    op.create_foreign_key(
        "fk_sogc_person_entities_merged_into",
        "sogc_person_entities", "sogc_person_entities",
        ["merged_into_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_sogc_person_entities_id", "sogc_person_entities", ["id"])
    op.create_index("ix_sogc_person_entities_normalized_key", "sogc_person_entities", ["normalized_key"], unique=True)
    op.create_index("ix_sogc_person_entities_confidence_level", "sogc_person_entities", ["confidence_level"])
    op.create_index("ix_sogc_person_entities_merged_into_id", "sogc_person_entities", ["merged_into_id"])

    # sogc_person_appearances
    op.create_table(
        "sogc_person_appearances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("person_entity_id", sa.Integer(),
                  sa.ForeignKey("sogc_person_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_override_id", sa.Integer(),
                  sa.ForeignKey("sogc_person_entities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sogc_change_id", sa.Integer(),
                  sa.ForeignKey("sogc_changes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sogc_publication_id", sa.Integer(),
                  sa.ForeignKey("sogc_publications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_uid", sa.String(20),
                  sa.ForeignKey("companies.uid", ondelete="SET NULL"), nullable=True),
        sa.Column("pub_date", sa.String(32), nullable=True),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("role", sa.String(256), nullable=True),
        sa.Column("role_category", sa.String(32), nullable=True),
        sa.Column("signature_type", sa.String(128), nullable=True),
        sa.Column("bisher_role", sa.String(256), nullable=True),
        sa.Column("residence_municipality", sa.String(256), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=True),
        sa.Column("title", sa.String(64), nullable=True),
        sa.Column("raw_excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sogc_person_appearances_id", "sogc_person_appearances", ["id"])
    op.create_index("ix_sogc_person_appearances_person_entity_id", "sogc_person_appearances", ["person_entity_id"])
    op.create_index("ix_sogc_person_appearances_entity_override_id", "sogc_person_appearances", ["entity_override_id"])
    op.create_index("ix_sogc_person_appearances_sogc_change_id", "sogc_person_appearances", ["sogc_change_id"])
    op.create_index("ix_sogc_person_appearances_sogc_publication_id", "sogc_person_appearances", ["sogc_publication_id"])
    op.create_index("ix_sogc_person_appearances_company_uid", "sogc_person_appearances", ["company_uid"])
    op.create_index("ix_sogc_person_appearances_pub_date", "sogc_person_appearances", ["pub_date"])
    op.create_index("ix_sogc_person_appearances_role_category", "sogc_person_appearances", ["role_category"])

    # sogc_auditors
    op.create_table(
        "sogc_auditors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sogc_change_id", sa.Integer(),
                  sa.ForeignKey("sogc_changes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sogc_publication_id", sa.Integer(),
                  sa.ForeignKey("sogc_publications.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_uid", sa.String(20),
                  sa.ForeignKey("companies.uid", ondelete="SET NULL"), nullable=True),
        sa.Column("pub_date", sa.String(32), nullable=True),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("auditor_name", sa.String(512), nullable=True),
        sa.Column("auditor_uid", sa.String(20), nullable=True),
        sa.Column("auditor_legal_form", sa.String(128), nullable=True),
        sa.Column("auditor_location", sa.String(256), nullable=True),
        sa.Column("auditor_name_normalized", sa.String(512), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sogc_auditors_id", "sogc_auditors", ["id"])
    op.create_index("ix_sogc_auditors_sogc_change_id", "sogc_auditors", ["sogc_change_id"])
    op.create_index("ix_sogc_auditors_sogc_publication_id", "sogc_auditors", ["sogc_publication_id"])
    op.create_index("ix_sogc_auditors_company_uid", "sogc_auditors", ["company_uid"])
    op.create_index("ix_sogc_auditors_pub_date", "sogc_auditors", ["pub_date"])
    op.create_index("ix_sogc_auditors_auditor_uid", "sogc_auditors", ["auditor_uid"])
    op.create_index("ix_sogc_auditors_auditor_name_normalized", "sogc_auditors", ["auditor_name_normalized"])

    # sogc_person_flags
    op.create_table(
        "sogc_person_flags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("flag_type", sa.String(32), nullable=False),
        sa.Column("primary_entity_id", sa.Integer(),
                  sa.ForeignKey("sogc_person_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("secondary_entity_id", sa.Integer(),
                  sa.ForeignKey("sogc_person_entities.id", ondelete="SET NULL"), nullable=True),
        sa.Column("appearance_id", sa.Integer(),
                  sa.ForeignKey("sogc_person_appearances.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("is_resolved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("resolution_action", sa.String(32), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reported_by_user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sogc_person_flags_id", "sogc_person_flags", ["id"])
    op.create_index("ix_sogc_person_flags_flag_type", "sogc_person_flags", ["flag_type"])
    op.create_index("ix_sogc_person_flags_primary_entity_id", "sogc_person_flags", ["primary_entity_id"])
    op.create_index("ix_sogc_person_flags_is_resolved", "sogc_person_flags", ["is_resolved"])


def downgrade() -> None:
    op.drop_table("sogc_person_flags")
    op.drop_table("sogc_auditors")
    op.drop_table("sogc_person_appearances")
    op.drop_constraint("fk_sogc_person_entities_merged_into", "sogc_person_entities", type_="foreignkey")
    op.drop_table("sogc_person_entities")
