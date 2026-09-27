# orders-db runbook

orders-db is the PostgreSQL database for orders. It has one primary and one replica.

## Database down

The primary accepts no connections and orders cannot be saved.

1. Confirm that the primary host is unreachable.
2. Promote the replica to primary.
3. Point the connection string at the new primary.

## Too many connections

The logs show error 53300 (too many connections).

1. Lower the PgBouncer pool size for the busiest client.
2. Close idle sessions.

## Replication lag

The replica is more than 30 seconds behind the primary. Check disk IO on the replica and pause heavy reports.
