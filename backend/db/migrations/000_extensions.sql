-- Extensions the schema depends on.
--
-- This file exists because the schema could not be rebuilt from the
-- repository. The extensions were installed by hand in an ad-hoc psql session
-- before 001 was first applied, so 001 worked on the developer's machine and
-- failed on an empty database at `type "citext" does not exist`.
--
-- Nothing in the running server was wrong, which is what makes the class of
-- defect dangerous: a migration set that only applies to a database that
-- already has undocumented state is a schema whose only real copy is the
-- production server. Found by backend/db/stress_test.sh, which rebuilds into
-- a throwaway database precisely so that this cannot stay true.
--
-- Each dependency, and what breaks without it:
--   pgcrypto   gen_random_uuid() for every primary key; digest() for the
--              content-addressed blob store's self-verification CHECK
--   citext     case-insensitive UNIQUE on names, codes and aliases, so
--              'Aspirin' and 'aspirin' cannot both register
--   vector     bit(2048) Jaccard operator + HNSW index = Tanimoto similarity
--              search without the RDKit cartridge
--   btree_gist exclusion constraints over mixed scalar/range columns
--   pg_trgm    fuzzy name lookup for the compound alias search path

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- meta.migration does not exist yet on a fresh database, so this file cannot
-- record itself the way the others do; 001 records it on the caller's behalf.
