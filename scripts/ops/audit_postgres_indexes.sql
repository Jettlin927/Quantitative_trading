\set ON_ERROR_STOP on

BEGIN READ ONLY;

WITH indexes AS (
  SELECT
    namespace.nspname AS schema_name,
    table_class.relname AS table_name,
    index_class.relname AS index_name,
    pg_index.indisunique,
    pg_index.indisprimary,
    pg_index.indkey,
    pg_index.indclass,
    pg_index.indcollation,
    pg_index.indoption,
    pg_get_expr(pg_index.indpred, pg_index.indrelid) AS predicate,
    pg_get_expr(pg_index.indexprs, pg_index.indrelid) AS expressions,
    pg_relation_size(index_class.oid) AS size_bytes
  FROM pg_index
  JOIN pg_class AS table_class ON table_class.oid = pg_index.indrelid
  JOIN pg_class AS index_class ON index_class.oid = pg_index.indexrelid
  JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
  WHERE namespace.nspname = 'public'
    AND table_class.relkind = 'r'
)
SELECT
  unique_index.table_name,
  unique_index.index_name AS unique_index,
  duplicate_index.index_name AS duplicate_index,
  pg_size_pretty(duplicate_index.size_bytes) AS duplicate_size,
  duplicate_index.size_bytes
FROM indexes AS unique_index
JOIN indexes AS duplicate_index
  ON duplicate_index.schema_name = unique_index.schema_name
 AND duplicate_index.table_name = unique_index.table_name
 AND duplicate_index.indkey = unique_index.indkey
 AND duplicate_index.indclass = unique_index.indclass
 AND duplicate_index.indcollation = unique_index.indcollation
 AND duplicate_index.indoption = unique_index.indoption
 AND duplicate_index.predicate IS NOT DISTINCT FROM unique_index.predicate
 AND duplicate_index.expressions IS NOT DISTINCT FROM unique_index.expressions
WHERE unique_index.indisunique
  AND NOT duplicate_index.indisunique
  AND NOT duplicate_index.indisprimary
ORDER BY duplicate_index.size_bytes DESC, unique_index.table_name;

ROLLBACK;
