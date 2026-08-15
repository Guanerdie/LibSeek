"""Add immutable conservative automation policy and decision auditing.

Revision ID: 20260811_0008
Revises: 20260811_0007
Create Date: 2026-08-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0008"
down_revision: str | None = "20260811_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_REVISION_ID = "00000000-0000-0000-0000-000000000008"
DEFAULT_POLICY_HASH = "c3daca9d3005065aae8712e956b2765a7460e23234f577b4671aa0b00e326814"
DEFAULT_ELIGIBILITY_RULES = (
    '{"identity_min_score": 0.5, "identity_min_margin": 0.1, '
    '"torrent_min_score": 0.75, "torrent_min_margin": 0.1, '
    '"torrent_min_seeders": 1}'
)
DEFAULT_ACKNOWLEDGEMENTS = (
    '{"acknowledges_hnr": false, "acknowledges_seeding": false, '
    '"acknowledges_plan_only": false, "acknowledges_add_paused_only": false}'
)


def automation_mode() -> sa.Enum:
    return sa.Enum(
        "DISABLED",
        "MANUAL",
        "AUTO_IF_ELIGIBLE",
        name="automationmode",
        native_enum=False,
        length=30,
    )


def automation_stage() -> sa.Enum:
    return sa.Enum(
        "IDENTITY",
        "TORRENT_SELECTION",
        "APPROVAL",
        "EXECUTION",
        name="automationstage",
        native_enum=False,
        length=30,
    )


def decision_outcome() -> sa.Enum:
    return sa.Enum(
        "ACTION_CREATED",
        "MANUAL_REQUIRED",
        "DISABLED",
        "BLOCKED",
        "STALE",
        "NOOP",
        name="decisionoutcome",
        native_enum=False,
        length=30,
    )


def origin() -> sa.Enum:
    return sa.Enum(
        "MANUAL",
        "AUTOMATION",
        name="origin",
        native_enum=False,
        length=20,
    )


def upgrade() -> None:
    op.drop_constraint(
        "ck_download_executions_submission_metadata",
        "download_executions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_download_executions_submission_metadata",
        "download_executions",
        "((status IN ('SUBMITTING', 'SUBMITTED', 'ALREADY_PRESENT', "
        "'OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED', 'RECONCILIATION_PENDING') "
        "AND actual_info_hash IS NOT NULL) OR (status = 'CANCELLED') OR "
        "(status NOT IN ('SUBMITTING', 'SUBMITTED', 'ALREADY_PRESENT', "
        "'OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED', 'RECONCILIATION_PENDING', "
        "'CANCELLED') AND actual_info_hash IS NULL))",
    )
    op.create_index(
        "uq_torrent_search_active_media_site",
        "torrent_search_runs",
        ["media_id", "site_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PT_SEARCH_PENDING', 'PT_SEARCHING')"),
        sqlite_where=sa.text("status IN ('PT_SEARCH_PENDING', 'PT_SEARCHING')"),
    )
    op.add_column(
        "metadata_matches", sa.Column("resolution_job_id", sa.String(36), nullable=True)
    )
    op.create_foreign_key(
        "fk_metadata_matches_resolution_job_id_jobs",
        "metadata_matches",
        "jobs",
        ["resolution_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_metadata_matches_resolution_job_id", "metadata_matches", ["resolution_job_id"]
    )

    op.create_table(
        "automation_policy_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("identity_mode", automation_mode(), nullable=False),
        sa.Column("torrent_selection_mode", automation_mode(), nullable=False),
        sa.Column("approval_mode", automation_mode(), nullable=False),
        sa.Column("execution_mode", automation_mode(), nullable=False),
        sa.Column("eligibility_rules", sa.JSON(), nullable=False),
        sa.Column("acknowledgements", sa.JSON(), nullable=False),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("previous_policy_hash", sa.String(64)),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revision_no >= 1", name="ck_automation_policy_revision_number"
        ),
        sa.CheckConstraint(
            "policy_hash ~ '^[0-9a-f]{64}$'",
            name="ck_automation_policy_revision_hash",
        ),
        sa.CheckConstraint(
            "previous_policy_hash IS NULL OR previous_policy_hash ~ '^[0-9a-f]{64}$'",
            name="ck_automation_policy_previous_hash",
        ),
        sa.CheckConstraint(
            "(revision_no = 1 AND previous_policy_hash IS NULL) OR "
            "(revision_no > 1 AND previous_policy_hash IS NOT NULL)",
            name="ck_automation_policy_hash_chain_shape",
        ),
        sa.UniqueConstraint("revision_no", name="uq_automation_policy_revision_no"),
        sa.UniqueConstraint("policy_hash", name="uq_automation_policy_hash"),
    )
    op.create_index(
        "ix_automation_policy_revisions_effective_from",
        "automation_policy_revisions",
        ["effective_from"],
    )
    op.create_index(
        "ix_automation_policy_revisions_created_at",
        "automation_policy_revisions",
        ["created_at"],
    )
    op.execute(
        f"""
        INSERT INTO automation_policy_revisions (
            id, revision_no, identity_mode, torrent_selection_mode,
            approval_mode, execution_mode, eligibility_rules, acknowledgements,
            policy_hash, previous_policy_hash, effective_from, created_by, created_at
        ) VALUES (
            '{DEFAULT_REVISION_ID}', 1, 'MANUAL', 'MANUAL', 'MANUAL', 'MANUAL',
            '{DEFAULT_ELIGIBILITY_RULES}',
            '{DEFAULT_ACKNOWLEDGEMENTS}',
            '{DEFAULT_POLICY_HASH}', NULL, CURRENT_TIMESTAMP, 'system:migration',
            CURRENT_TIMESTAMP
        )
        """
    )

    op.create_table(
        "automation_policy_heads",
        sa.Column("scope", sa.String(30), primary_key=True),
        sa.Column(
            "current_revision_id",
            sa.String(36),
            sa.ForeignKey("automation_policy_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("scope = 'global'", name="ck_automation_policy_head_scope"),
        sa.CheckConstraint("version >= 1", name="ck_automation_policy_head_version"),
        sa.UniqueConstraint(
            "current_revision_id", name="uq_automation_policy_head_current_revision"
        ),
    )
    op.execute(
        "INSERT INTO automation_policy_heads (scope, current_revision_id, version) "
        f"VALUES ('global', '{DEFAULT_REVISION_ID}', 1)"
    )

    op.create_table(
        "automation_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "policy_revision_id",
            sa.String(36),
            sa.ForeignKey("automation_policy_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("stage", automation_stage(), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("outcome", decision_outcome(), nullable=False),
        sa.Column(
            "media_item_id",
            sa.String(36),
            sa.ForeignKey("media_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "metadata_match_id",
            sa.String(36),
            sa.ForeignKey("metadata_matches.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "torrent_candidate_id",
            sa.String(36),
            sa.ForeignKey("torrent_candidates.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "approval_request_id",
            sa.String(36),
            sa.ForeignKey("approval_requests.id", ondelete="RESTRICT"),
        ),
        sa.Column("download_execution_id", sa.String(36)),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("evidence_snapshot", sa.JSON(), nullable=False),
        sa.Column("evidence_hash", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "evidence_hash ~ '^[0-9a-f]{64}$'",
            name="ck_automation_decision_evidence_hash",
        ),
        sa.CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'",
            name="ck_automation_decision_dedupe_key",
        ),
        sa.UniqueConstraint("dedupe_key", name="uq_automation_decision_dedupe_key"),
    )
    for column_name in (
        "policy_revision_id",
        "stage",
        "outcome",
        "media_item_id",
        "metadata_match_id",
        "torrent_candidate_id",
        "approval_request_id",
        "download_execution_id",
        "created_at",
    ):
        op.create_index(
            f"ix_automation_decisions_{column_name}",
            "automation_decisions",
            [column_name],
        )

    for table_name in ("execution_intents", "download_executions"):
        op.add_column(
            table_name,
            sa.Column(
                "origin",
                origin(),
                nullable=False,
                server_default=sa.text("'MANUAL'"),
            ),
        )
        op.add_column(
            table_name,
            sa.Column("automation_policy_revision_id", sa.String(36), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column("automation_decision_id", sa.String(36), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table_name}_automation_policy_revision_id",
            table_name,
            "automation_policy_revisions",
            ["automation_policy_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_foreign_key(
            f"fk_{table_name}_automation_decision_id",
            table_name,
            "automation_decisions",
            ["automation_decision_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        op.create_index(
            f"ix_{table_name}_automation_policy_revision_id",
            table_name,
            ["automation_policy_revision_id"],
        )
        op.create_index(
            f"ix_{table_name}_automation_decision_id",
            table_name,
            ["automation_decision_id"],
        )
        op.create_check_constraint(
            f"ck_{table_name}_automation_binding",
            table_name,
            "((origin = 'MANUAL' AND automation_policy_revision_id IS NULL "
            "AND automation_decision_id IS NULL) OR "
            "(origin = 'AUTOMATION' AND automation_policy_revision_id IS NOT NULL "
            "AND automation_decision_id IS NOT NULL AND launch_mode = 'ADD_PAUSED'))",
        )

    op.execute(
        """
        CREATE FUNCTION unin_reject_automation_audit_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'automation policy and decision records are append-only'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE FUNCTION unin_guard_automation_policy_head()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'automation policy head cannot be deleted'
                    USING ERRCODE = '55000';
            END IF;
            IF NEW.scope IS DISTINCT FROM OLD.scope
                OR NEW.current_revision_id IS NOT DISTINCT FROM OLD.current_revision_id
                OR NEW.version <> OLD.version + 1
                OR NOT EXISTS (
                    SELECT 1
                    FROM automation_policy_revisions next_revision
                    JOIN automation_policy_revisions previous_revision
                        ON previous_revision.id = OLD.current_revision_id
                    WHERE next_revision.id = NEW.current_revision_id
                        AND next_revision.revision_no = NEW.version
                        AND next_revision.previous_policy_hash = previous_revision.policy_hash
                )
            THEN
                RAISE EXCEPTION 'automation policy head must advance atomically by one'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE FUNCTION unin_guard_execution_automation_binding()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.origin IS DISTINCT FROM OLD.origin
                OR NEW.automation_policy_revision_id IS DISTINCT FROM
                    OLD.automation_policy_revision_id
                OR NEW.automation_decision_id IS DISTINCT FROM OLD.automation_decision_id
            THEN
                RAISE EXCEPTION 'execution automation binding fields cannot change'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_automation_policy_revision_immutable "
        "BEFORE UPDATE OR DELETE ON automation_policy_revisions FOR EACH ROW "
        "EXECUTE FUNCTION unin_reject_automation_audit_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_automation_decision_immutable "
        "BEFORE UPDATE OR DELETE ON automation_decisions FOR EACH ROW "
        "EXECUTE FUNCTION unin_reject_automation_audit_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_automation_policy_head_guard "
        "BEFORE UPDATE OR DELETE ON automation_policy_heads FOR EACH ROW "
        "EXECUTE FUNCTION unin_guard_automation_policy_head()"
    )
    op.execute(
        "CREATE TRIGGER trg_execution_intent_automation_binding_immutable "
        "BEFORE UPDATE ON execution_intents FOR EACH ROW "
        "EXECUTE FUNCTION unin_guard_execution_automation_binding()"
    )
    op.execute(
        "CREATE TRIGGER trg_download_execution_automation_binding_immutable "
        "BEFORE UPDATE ON download_executions FOR EACH ROW "
        "EXECUTE FUNCTION unin_guard_execution_automation_binding()"
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM automation_decisions)
                OR (SELECT count(*) FROM automation_policy_revisions) <> 1
                OR (SELECT count(*) FROM automation_policy_heads) <> 1
                OR EXISTS (
                    SELECT 1 FROM automation_policy_revisions
                    WHERE revision_no <> 1 OR id <> '{DEFAULT_REVISION_ID}'
                        OR policy_hash <> '{DEFAULT_POLICY_HASH}'
                        OR previous_policy_hash IS NOT NULL
                        OR identity_mode <> 'MANUAL'
                        OR torrent_selection_mode <> 'MANUAL'
                        OR approval_mode <> 'MANUAL'
                        OR execution_mode <> 'MANUAL'
                )
                OR EXISTS (
                    SELECT 1 FROM automation_policy_heads
                    WHERE scope <> 'global' OR version <> 1
                        OR current_revision_id <> '{DEFAULT_REVISION_ID}'
                )
                OR EXISTS (
                    SELECT 1 FROM execution_intents WHERE origin = 'AUTOMATION'
                )
                OR EXISTS (
                    SELECT 1 FROM download_executions WHERE origin = 'AUTOMATION'
                )
            THEN
                RAISE EXCEPTION
                    'cannot downgrade with automation audit or custom policy records'
                    USING ERRCODE = '55000';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_download_execution_automation_binding_immutable "
        "ON download_executions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_execution_intent_automation_binding_immutable "
        "ON execution_intents"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_automation_policy_head_guard ON automation_policy_heads"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_automation_decision_immutable ON automation_decisions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_automation_policy_revision_immutable "
        "ON automation_policy_revisions"
    )
    op.execute("DROP FUNCTION IF EXISTS unin_guard_execution_automation_binding()")
    op.execute("DROP FUNCTION IF EXISTS unin_guard_automation_policy_head()")
    op.execute("DROP FUNCTION IF EXISTS unin_reject_automation_audit_mutation()")

    op.drop_constraint(
        "ck_download_executions_submission_metadata",
        "download_executions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_download_executions_submission_metadata",
        "download_executions",
        "((status IN ('SUBMITTING', 'SUBMITTED', 'ALREADY_PRESENT', "
        "'OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED', 'RECONCILIATION_PENDING') "
        "AND actual_info_hash IS NOT NULL) OR "
        "(status NOT IN ('SUBMITTING', 'SUBMITTED', 'ALREADY_PRESENT', "
        "'OUTCOME_UNKNOWN', 'RECONCILIATION_REQUIRED', 'RECONCILIATION_PENDING') "
        "AND actual_info_hash IS NULL))",
    )

    for table_name in ("download_executions", "execution_intents"):
        op.drop_constraint(
            f"ck_{table_name}_automation_binding", table_name, type_="check"
        )
        op.drop_index(
            f"ix_{table_name}_automation_decision_id", table_name=table_name
        )
        op.drop_index(
            f"ix_{table_name}_automation_policy_revision_id", table_name=table_name
        )
        op.drop_constraint(
            f"fk_{table_name}_automation_decision_id", table_name, type_="foreignkey"
        )
        op.drop_constraint(
            f"fk_{table_name}_automation_policy_revision_id",
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "automation_decision_id")
        op.drop_column(table_name, "automation_policy_revision_id")
        op.drop_column(table_name, "origin")

    op.drop_table("automation_decisions")
    op.drop_table("automation_policy_heads")
    op.drop_table("automation_policy_revisions")
    op.drop_index("ix_metadata_matches_resolution_job_id", table_name="metadata_matches")
    op.drop_constraint(
        "fk_metadata_matches_resolution_job_id_jobs",
        "metadata_matches",
        type_="foreignkey",
    )
    op.drop_column("metadata_matches", "resolution_job_id")
    op.drop_index(
        "uq_torrent_search_active_media_site", table_name="torrent_search_runs"
    )
