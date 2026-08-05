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
        'quant_personal_analysis'
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

DO $database_grants$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'quant_api_runtime',
        'quant_research_runtime',
        'quant_personal_api',
        'quant_personal_analysis'
    ]
    LOOP
        EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), role_name);
    END LOOP;
END
$database_grants$;

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

REVOKE CREATE ON SCHEMA public FROM
    quant_api_runtime,
    quant_research_runtime,
    quant_personal_api,
    quant_personal_analysis;
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
