# payments-api runbook

payments-api takes card payments for checkout. It calls the payment provider and the orders database.

## 5xx errors

HTTP 500, 502 and 503 responses from payments-api.

1. Check the last deploy. If the errors started after it, roll back (see Rollback).
2. Search the logs for PSP_GATEWAY_TIMEOUT. It means the payment provider did not answer in time.
3. Check the database connection pool. A full pool gives 503 responses.

## High latency

A p95 above 1000 ms makes checkout slow.

1. Look for connection pool saturation on the dashboard.
2. Scale out: add two instances.

## Rollback

1. Find the previous release tag.
2. Redeploy it with the deploy pipeline.
3. Watch the error rate for ten minutes.
