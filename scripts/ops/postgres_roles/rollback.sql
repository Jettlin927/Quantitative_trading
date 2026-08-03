\set ON_ERROR_STOP on

BEGIN;

ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA private_workbench
    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES
    FROM quant_personal_api, quant_personal_analysis;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA private_workbench
    REVOKE USAGE, SELECT, UPDATE ON SEQUENCES
    FROM quant_personal_api, quant_personal_analysis;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES
    FROM quant_api_runtime, quant_sync_runtime, quant_research_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
    REVOKE USAGE, SELECT, UPDATE ON SEQUENCES
    FROM quant_api_runtime, quant_sync_runtime, quant_research_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
    REVOKE SELECT ON TABLES FROM quant_personal_analysis;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
    REVOKE SELECT ON SEQUENCES FROM quant_personal_analysis;

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA private_workbench FROM
    quant_api_runtime,
    quant_sync_runtime,
    quant_research_runtime,
    quant_personal_api,
    quant_personal_analysis;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA private_workbench FROM
    quant_api_runtime,
    quant_sync_runtime,
    quant_research_runtime,
    quant_personal_api,
    quant_personal_analysis;
REVOKE ALL PRIVILEGES ON SCHEMA private_workbench FROM
    quant_api_runtime,
    quant_sync_runtime,
    quant_research_runtime,
    quant_personal_api,
    quant_personal_analysis;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM
    quant_api_runtime,
    quant_sync_runtime,
    quant_research_runtime,
    quant_personal_api,
    quant_personal_analysis;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM
    quant_api_runtime,
    quant_sync_runtime,
    quant_research_runtime,
    quant_personal_api,
    quant_personal_analysis;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM
    quant_api_runtime,
    quant_sync_runtime,
    quant_research_runtime,
    quant_personal_api,
    quant_personal_analysis;

DO $database_revokes$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'quant_api_runtime',
        'quant_sync_runtime',
        'quant_research_runtime',
        'quant_personal_api',
        'quant_personal_analysis'
    ]
    LOOP
        EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM %I', current_database(), role_name);
    END LOOP;
END
$database_revokes$;

DROP ROLE IF EXISTS quant_personal_analysis;
DROP ROLE IF EXISTS quant_personal_api;
DROP ROLE IF EXISTS quant_research_runtime;
DROP ROLE IF EXISTS quant_sync_runtime;
DROP ROLE IF EXISTS quant_api_runtime;

COMMIT;
