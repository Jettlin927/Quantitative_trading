\set ON_ERROR_STOP on

SELECT
    rolname,
    rolcanlogin,
    rolsuper,
    rolcreatedb,
    rolcreaterole,
    rolreplication,
    rolbypassrls,
    rolinherit
FROM pg_roles
WHERE rolname IN (
    'quant_api_runtime',
    'quant_sync_runtime',
    'quant_research_runtime',
    'quant_personal_api',
    'quant_personal_analysis'
)
ORDER BY rolname;

SELECT
    role_name,
    has_database_privilege(role_name, current_database(), 'CONNECT') AS database_connect,
    has_schema_privilege(role_name, 'public', 'USAGE') AS public_usage,
    has_schema_privilege(role_name, 'public', 'CREATE') AS public_create,
    has_schema_privilege(role_name, 'private_workbench', 'USAGE') AS private_usage,
    has_table_privilege(role_name, 'public.stocks', 'SELECT') AS public_select,
    has_table_privilege(role_name, 'private_workbench.personal_workspaces', 'SELECT') AS private_select,
    has_table_privilege(role_name, 'private_workbench.personal_workspaces', 'INSERT')
        AND has_table_privilege(role_name, 'private_workbench.personal_workspaces', 'UPDATE')
        AND has_table_privilege(role_name, 'private_workbench.personal_workspaces', 'DELETE') AS private_write
FROM unnest(ARRAY[
    'quant_api_runtime',
    'quant_sync_runtime',
    'quant_research_runtime',
    'quant_personal_api',
    'quant_personal_analysis'
]) AS role_name
ORDER BY role_name;

SELECT
    defaclrole::regrole::text AS owner_role,
    coalesce(nspname, '-') AS schema_name,
    defaclobjtype,
    defaclacl::text AS acl
FROM pg_default_acl
LEFT JOIN pg_namespace ON pg_namespace.oid = pg_default_acl.defaclnamespace
WHERE nspname IN ('public', 'private_workbench')
ORDER BY owner_role, schema_name, defaclobjtype;
