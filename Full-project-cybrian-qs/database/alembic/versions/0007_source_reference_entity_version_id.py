"""Add entity_version_id to source_references for version-scoped semantic cleanup.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "source_references" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("source_references")}
    if "entity_version_id" not in existing:
        op.add_column(
            "source_references",
            sa.Column("entity_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_index(
            "ix_source_references_entity_version_id",
            "source_references",
            ["entity_version_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "source_references" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("source_references")}
    if "entity_version_id" in existing:
        try:
            op.drop_index("ix_source_references_entity_version_id", table_name="source_references")
        except Exception:
            pass
        try:
            op.drop_column("source_references", "entity_version_id")
        except Exception:
            pass
