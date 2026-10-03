#!/bin/sh
# pgAdmin needs a 0600 passfile owned by its own user, which a read-only bind mount can't
# provide. Render it from the environment, then hand over to the stock entrypoint.
set -eu
mkdir -p /var/lib/pgadmin/storage/gridcast
umask 077
cat > /var/lib/pgadmin/pgpass <<PGPASS
gridcast-postgres:5432:*:gridcast_owner:${GRIDCAST_OWNER_PASSWORD}
gridcast-postgres:5432:*:gridcast_readonly:${GRIDCAST_READONLY_PASSWORD}
gridcast-postgres:5432:*:${POSTGRES_USER}:${POSTGRES_PASSWORD}
PGPASS
exec /entrypoint.sh "$@"
