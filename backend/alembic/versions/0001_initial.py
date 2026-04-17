"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-17

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("timezone", sa.String(length=60), nullable=False),
        sa.Column("slot_duration_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("booking_horizon_days", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("min_lead_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
        sa.CheckConstraint("slot_duration_minutes > 0", name="ck_organizations_slot_duration_positive"),
        sa.CheckConstraint("booking_horizon_days > 0", name="ck_organizations_horizon_positive"),
        sa.CheckConstraint("min_lead_minutes >= 0", name="ck_organizations_lead_non_negative"),
    )
    # BigInteger PK needs an explicit identity sequence
    op.execute("CREATE SEQUENCE organizations_id_seq OWNED BY organizations.id")
    op.execute("ALTER TABLE organizations ALTER COLUMN id SET DEFAULT nextval('organizations_id_seq')")
    op.create_index("ix_organizations_slug", "organizations", ["slug"])

    user_role = postgresql.ENUM("admin", "user", name="user_role", create_type=False)
    user_role.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_users_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "email", name="uq_users_org_email"),
    )
    op.execute("CREATE SEQUENCE users_id_seq OWNED BY users.id")
    op.execute("ALTER TABLE users ALTER COLUMN id SET DEFAULT nextval('users_id_seq')")
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_refresh_tokens"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_refresh_tokens_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.execute("CREATE SEQUENCE refresh_tokens_id_seq OWNED BY refresh_tokens.id")
    op.execute("ALTER TABLE refresh_tokens ALTER COLUMN id SET DEFAULT nextval('refresh_tokens_id_seq')")
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"])
    op.create_index("ix_refresh_tokens_token_family_id", "refresh_tokens", ["token_family_id"])
    op.create_index(
        "ix_refresh_tokens_user_id_revoked_at",
        "refresh_tokens",
        ["user_id", "revoked_at"],
    )

    op.create_table(
        "availability_rules",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
        sa.Column("start_local_time", sa.Time(timezone=False), nullable=False),
        sa.Column("end_local_time", sa.Time(timezone=False), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_availability_rules"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_availability_rules_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_availability_rules_dow_range"),
        sa.CheckConstraint(
            "end_local_time > start_local_time", name="ck_availability_rules_end_after_start"
        ),
    )
    op.execute("CREATE SEQUENCE availability_rules_id_seq OWNED BY availability_rules.id")
    op.execute("ALTER TABLE availability_rules ALTER COLUMN id SET DEFAULT nextval('availability_rules_id_seq')")
    op.create_index(
        "ix_availability_rules_organization_id_day_of_week",
        "availability_rules",
        ["organization_id", "day_of_week"],
    )

    exception_kind = postgresql.ENUM("full_day", "range", name="exception_kind", create_type=False)
    exception_kind.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "exceptions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("start_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("kind", exception_kind, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_exceptions"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_exceptions_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("end_at_utc > start_at_utc", name="ck_exceptions_end_after_start"),
    )
    op.execute("CREATE SEQUENCE exceptions_id_seq OWNED BY exceptions.id")
    op.execute("ALTER TABLE exceptions ALTER COLUMN id SET DEFAULT nextval('exceptions_id_seq')")
    op.create_index(
        "ix_exceptions_organization_id_start_end",
        "exceptions",
        ["organization_id", "start_at_utc", "end_at_utc"],
    )

    booking_status = postgresql.ENUM("active", "cancelled", name="booking_status", create_type=False)
    booking_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "bookings",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("start_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", booking_status, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_bookings"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_bookings_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_bookings_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"], ["users.id"],
            name="fk_bookings_cancelled_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("end_at_utc > start_at_utc", name="ck_bookings_end_after_start"),
    )
    op.execute("CREATE SEQUENCE bookings_id_seq OWNED BY bookings.id")
    op.execute("ALTER TABLE bookings ALTER COLUMN id SET DEFAULT nextval('bookings_id_seq')")
    op.create_index(
        "ix_bookings_organization_id_start_at_utc",
        "bookings",
        ["organization_id", "start_at_utc"],
    )
    op.create_index(
        "ix_bookings_organization_id_user_id",
        "bookings",
        ["organization_id", "user_id"],
    )
    # ⭐ RF-4.2: atomic concurrency guarantee for active bookings on the same slot.
    op.execute(
        """
        CREATE UNIQUE INDEX uniq_active_booking_slot
        ON bookings (organization_id, start_at_utc)
        WHERE status = 'active'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uniq_active_booking_slot")
    op.drop_index("ix_bookings_organization_id_user_id", table_name="bookings")
    op.drop_index("ix_bookings_organization_id_start_at_utc", table_name="bookings")
    op.drop_table("bookings")
    op.execute("DROP SEQUENCE IF EXISTS bookings_id_seq")
    op.execute("DROP TYPE IF EXISTS booking_status")

    op.drop_index("ix_exceptions_organization_id_start_end", table_name="exceptions")
    op.drop_table("exceptions")
    op.execute("DROP SEQUENCE IF EXISTS exceptions_id_seq")
    op.execute("DROP TYPE IF EXISTS exception_kind")

    op.drop_index(
        "ix_availability_rules_organization_id_day_of_week",
        table_name="availability_rules",
    )
    op.drop_table("availability_rules")
    op.execute("DROP SEQUENCE IF EXISTS availability_rules_id_seq")

    op.drop_index("ix_refresh_tokens_user_id_revoked_at", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_token_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.execute("DROP SEQUENCE IF EXISTS refresh_tokens_id_seq")

    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_table("users")
    op.execute("DROP SEQUENCE IF EXISTS users_id_seq")
    op.execute("DROP TYPE IF EXISTS user_role")

    op.drop_index("ix_organizations_slug", table_name="organizations")
    op.drop_table("organizations")
    op.execute("DROP SEQUENCE IF EXISTS organizations_id_seq")
