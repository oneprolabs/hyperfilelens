#!/bin/bash
set -e

# Grant schema privileges to the superuser (created by POSTGRES_USER)
# This script uses the POSTGRES_USER environment variable to grant privileges
# to the correct user, regardless of what name was set
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "${POSTGRES_DB:-sourcelens}" <<-EOSQL
    -- Grant schema privileges to the superuser
    GRANT ALL ON SCHEMA public TO "$POSTGRES_USER";
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "$POSTGRES_USER";
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "$POSTGRES_USER";
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO "$POSTGRES_USER";
EOSQL
