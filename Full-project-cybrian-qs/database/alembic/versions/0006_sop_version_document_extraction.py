"""SOP version document extraction columns (local Marker PDF pipeline).

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "sop_versions" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("sop_versions")}

    def add(name: str, column: sa.Column) -> None:
        if name not in existing:
            op.add_column("sop_versions", column)

    add("extraction_status", sa.Column("extraction_status", sa.String(length=50), nullable=True))
    add("extraction_engine", sa.Column("extraction_engine", sa.String(length=50), nullable=True))
    add("extraction_job_id", sa.Column("extraction_job_id", sa.String(length=100), nullable=True))
    add("extracted_markdown", sa.Column("extracted_markdown", sa.Text(), nullable=True))
    add("extracted_json", sa.Column("extracted_json", sa.JSON(), nullable=True))
    add("extraction_error", sa.Column("extraction_error", sa.Text(), nullable=True))
    add("extraction_cache_key", sa.Column("extraction_cache_key", sa.String(length=64), nullable=True))
    add(
        "extraction_started_at",
        sa.Column("extraction_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    add(
        "extraction_completed_at",
        sa.Column("extraction_completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "sop_versions" not in insp.get_table_names():
        return
    for col in (
        "extraction_completed_at",
        "extraction_started_at",
        "extraction_cache_key",
        "extraction_error",
        "extracted_json",
        "extracted_markdown",
        "extraction_job_id",
        "extraction_engine",
        "extraction_status",
    ):
        try:
            op.drop_column("sop_versions", col)
        except Exception:
            pass
