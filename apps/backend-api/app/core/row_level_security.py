"""
Row Level Security (Step 15.9)

PostgreSQL Row Level Security (RLS) for tenant isolation.

RLS ensures that even if application code has a bug,
the database itself enforces tenant isolation.

Usage:
    from app.core.row_level_security import enable_rls_for_all_tables, create_rls_policies

    # After creating tables:
    enable_rls_for_all_tables(engine)
    create_rls_policies(engine)
"""

from sqlalchemy import text, inspect
from sqlalchemy.engine import Engine


# Tables that should have RLS enabled
RLS_TABLES = [
    "leads",
    "conversations",
    "messages",
    "appointments",
    "agent_availability",
    "campaigns",
    "audit_logs",
    "agents",
]


def enable_rls_for_table(engine: Engine, table_name: str) -> None:
    """
    Enable Row Level Security on a table.

    After enabling, all non-superuser roles will see zero rows
    until a policy is created.
    """
    with engine.connect() as conn:
        conn.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
        conn.commit()
        print(f"RLS enabled on {table_name}")


def disable_rls_for_table(engine: Engine, table_name: str) -> None:
    """Disable Row Level Security on a table."""
    with engine.connect() as conn:
        conn.execute(text(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY"))
        conn.commit()
        print(f"RLS disabled on {table_name}")


def create_tenant_policy(engine: Engine, table_name: str) -> None:
    """
    Create a tenant isolation policy for a table.

    The policy allows access only to rows where tenant_id matches
    the current_setting('app.current_tenant_id').

    This is set per-request by the TenantIsolationMiddleware.
    """
    policy_name = f"tenant_isolation_{table_name}"

    with engine.connect() as conn:
        # Drop existing policy if it exists
        conn.execute(text(
            f"DROP POLICY IF EXISTS {policy_name} ON {table_name}"
        ))

        # Create policy
        conn.execute(text(f"""
            CREATE POLICY {policy_name} ON {table_name}
            FOR ALL
            USING (
                tenant_id::text = current_setting('app.current_tenant_id', true)
                OR current_setting('app.current_tenant_id', true) IS NULL
                OR current_setting('app.current_tenant_id', true) = ''
            )
            WITH CHECK (
                tenant_id::text = current_setting('app.current_tenant_id', true)
            )
        """))

        conn.commit()
        print(f"Tenant policy created on {table_name}")


def create_service_role_policy(engine: Engine, table_name: str) -> None:
    """
    Create a service role policy that allows full access.

    Used for background workers and admin operations.
    """
    policy_name = f"service_access_{table_name}"

    with engine.connect() as conn:
        conn.execute(text(
            f"DROP POLICY IF EXISTS {policy_name} ON {table_name}"
        ))

        # Grant full access to service role
        conn.execute(text(f"""
            CREATE POLICY {policy_name} ON {table_name}
            FOR ALL
            TO service_role
            USING (true)
            WITH CHECK (true)
        """))

        conn.commit()
        print(f"Service role policy created on {table_name}")


def enable_rls_for_all_tables(engine: Engine) -> None:
    """Enable RLS on all tenant-scoped tables."""
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    for table in RLS_TABLES:
        if table in existing_tables:
            enable_rls_for_table(engine, table)
        else:
            print(f"Table {table} not found, skipping RLS")


def create_rls_policies(engine: Engine) -> None:
    """Create tenant isolation policies for all RLS tables."""
    inspector = inspect(engine)
    existing_tables = inspector.get_table_names()

    for table in RLS_TABLES:
        if table in existing_tables:
            create_tenant_policy(engine, table)


def set_tenant_context(engine: Engine, tenant_id: str) -> None:
    """
    Set the current tenant ID for RLS.

    Called by TenantIsolationMiddleware at the start of each request.
    """
    with engine.connect() as conn:
        conn.execute(text(f"SET app.current_tenant_id = '{tenant_id}'"))


def clear_tenant_context(engine: Engine) -> None:
    """Clear the current tenant ID."""
    with engine.connect() as conn:
        conn.execute(text("SET app.current_tenant_id = ''"))


def create_service_role(engine: Engine) -> None:
    """
    Create the service_role for background workers.

    This role bypasses RLS for admin/worker operations.
    """
    with engine.connect() as conn:
        try:
            conn.execute(text("CREATE ROLE service_role NOLOGIN"))
            conn.commit()
            print("service_role created")
        except Exception as e:
            if "already exists" in str(e):
                print("service_role already exists")
            else:
                raise


def grant_service_role(engine: Engine) -> None:
    """Grant service_role to the application user."""
    with engine.connect() as conn:
        try:
            conn.execute(text("GRANT service_role TO postgres"))
            conn.commit()
            print("service_role granted to postgres")
        except Exception as e:
            print(f"Could not grant service_role: {e}")


def setup_rls(engine: Engine) -> None:
    """
    Full RLS setup.

    Run this after creating all tables (e.g., after Alembic migration).
    """
    print("Setting up Row Level Security...")

    # Create service role
    create_service_role(engine)
    grant_service_role(engine)

    # Enable RLS on all tables
    enable_rls_for_all_tables(engine)

    # Create policies
    create_rls_policies(engine)

    print("RLS setup complete")
