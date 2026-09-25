"""brief created_at timezone aware

Make briefs.created_at match reports.generated_at.

WHY NO `USING` CLAUSE

    The obvious worry with TIMESTAMP -> TIMESTAMPTZ is that Postgres has to
    decide what zone the existing naive values are in, and gets it wrong. Here
    it does not, because of how they were written.

    created_at defaults to now(), which in Postgres returns timestamptz.
    Assigning that to a naive TIMESTAMP column casts it into the session
    timezone and discards the offset - so the stored values are already in the
    database's own timezone, not UTC. Verified on a local database whose
    timezone is America/New_York: a row inserted when UTC was 19:02 stored
    15:02.

    A bare ALTER ... TYPE timestamptz interprets naive values using that same
    session timezone, which is exactly the zone they were written in. So the
    default conversion is the correct one, and adding `USING created_at AT
    TIME ZONE 'UTC'` - the reflex - would corrupt every existing row by the
    server's offset from UTC.

    This is also the argument for the change itself: a naive column is a value
    whose meaning depends on a server setting that nothing records.

Revision ID: 46498d1ab364
Revises: 75572424621f
Create Date: 2026-09-24 15:02:30.239327

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '46498d1ab364'
down_revision: Union[str, Sequence[str], None] = '75572424621f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column('briefs', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False,
               existing_server_default=sa.text('now()'))


def downgrade() -> None:
    """Downgrade schema.

    Loses the offset, returning values to the session timezone. Reversible in
    shape, not in information - which is the point of having made the change.
    """
    op.alter_column('briefs', 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False,
               existing_server_default=sa.text('now()'))
