"""enable row level security

Revision ID: ef363f93bb84
Revises: 5aa8b200bbd2
Create Date: 2026-11-06 21:15:05.400073

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ef363f93bb84'
down_revision: Union[str, None] = '5aa8b200bbd2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Only the backend (postgres role, which bypasses RLS) touches these tables. With RLS on and no
    policies, Supabase's public REST API (anon / authenticated keys) can neither read nor write them."""
    op.execute("""
        DO $$ DECLARE t text; BEGIN
          FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
          END LOOP;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ DECLARE t text; BEGIN
          FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
            EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', t);
          END LOOP;
        END $$;
    """)
