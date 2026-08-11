\set ON_ERROR_STOP on

BEGIN;

DO $roles$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'quant_api_runtime',
        'quant_research_runtime',
        'quant_personal_api',
        'quant_personal_analysis',
        'quant_personal_mcp'
    ]
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'CREATE ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD NULL',
                role_name
            );
        ELSE
            EXECUTE format(
                'ALTER ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
                role_name
            );
        END IF;
    END LOOP;
END
$roles$;

DO $mcp_memberships$
DECLARE
    granted_role text;
BEGIN
    FOR granted_role IN
        SELECT granted.rolname
        FROM pg_auth_members
        JOIN pg_roles AS member ON member.oid = pg_auth_members.member
        JOIN pg_roles AS granted ON granted.oid = pg_auth_members.roleid
        WHERE member.rolname = 'quant_personal_mcp'
    LOOP
        EXECUTE format('REVOKE %I FROM quant_personal_mcp', granted_role);
    END LOOP;
END
$mcp_memberships$;

DO $database_grants$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'quant_api_runtime',
        'quant_research_runtime',
        'quant_personal_api',
        'quant_personal_analysis',
        'quant_personal_mcp'
    ]
    LOOP
        EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), role_name);
    END LOOP;
END
$database_grants$;

DO $database_public_revokes$
BEGIN
    EXECUTE format(
        'REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC',
        current_database()
    );
END
$database_public_revokes$;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA private_workbench FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA private_workbench FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA private_workbench FROM PUBLIC;

GRANT USAGE ON SCHEMA public TO quant_api_runtime, quant_research_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
    TO quant_api_runtime, quant_research_runtime;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public
    TO quant_api_runtime, quant_research_runtime;

GRANT USAGE ON SCHEMA public TO quant_personal_analysis;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO quant_personal_analysis;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO quant_personal_analysis;

GRANT USAGE ON SCHEMA private_workbench TO quant_personal_api, quant_personal_analysis;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA private_workbench
    TO quant_personal_api, quant_personal_analysis;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA private_workbench
    TO quant_personal_api, quant_personal_analysis;

REVOKE ALL ON SCHEMA public FROM quant_personal_mcp;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM quant_personal_mcp;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM quant_personal_mcp;
REVOKE ALL ON SCHEMA private_workbench FROM quant_personal_mcp;
REVOKE ALL ON ALL TABLES IN SCHEMA private_workbench FROM quant_personal_mcp;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA private_workbench FROM quant_personal_mcp;
GRANT USAGE ON SCHEMA private_workbench TO quant_personal_mcp;
GRANT SELECT ON TABLE
    private_workbench.personal_workspaces,
    private_workbench.personal_holdings,
    private_workbench.personal_instrument_states,
    private_workbench.personal_rule_revisions,
    private_workbench.personal_rule_evaluations,
    private_workbench.personal_tool_evidence_records,
    private_workbench.personal_capability_audit_events
    TO quant_personal_mcp;
GRANT INSERT ON TABLE
    private_workbench.personal_tool_evidence_records,
    private_workbench.personal_capability_audit_events
    TO quant_personal_mcp;

REVOKE CREATE ON SCHEMA public FROM
    quant_api_runtime,
    quant_research_runtime,
    quant_personal_api,
    quant_personal_analysis,
    quant_personal_mcp;
REVOKE ALL ON SCHEMA private_workbench FROM
    quant_api_runtime,
    quant_research_runtime;
REVOKE ALL ON ALL TABLES IN SCHEMA private_workbench FROM
    quant_api_runtime,
    quant_research_runtime;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA private_workbench FROM
    quant_api_runtime,
    quant_research_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES
    TO quant_api_runtime, quant_research_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES
    TO quant_api_runtime, quant_research_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
    GRANT SELECT ON TABLES TO quant_personal_analysis;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO quant_personal_analysis;

ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA private_workbench
    REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA private_workbench
    REVOKE ALL ON SEQUENCES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA private_workbench
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES
    TO quant_personal_api, quant_personal_analysis;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA private_workbench
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES
    TO quant_personal_api, quant_personal_analysis;

COMMIT;
