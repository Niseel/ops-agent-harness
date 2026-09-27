# search-api runbook

## Slow queries

Search requests take more than 2 seconds.

1. Find the slowest queries in the slow log.
2. Add a filter or a smaller page size.

## Circuit breaker errors

The cluster answers with CircuitBreakingException when a query needs too much memory.

1. Lower the result window for heavy queries.
2. Add heap memory to the data nodes.

## Reindexing

1. Create a new index with the new mapping.
2. Copy the documents with the reindex API.
3. Switch the alias to the new index.
