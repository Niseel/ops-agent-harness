# inventory-service runbook

## Stock mismatch

Stock counts differ between the warehouse and the shop. The logs show SKU_SYNC_CONFLICT.

1. Run the stock reconcile job for the affected SKUs.
2. Lock manual edits until the job ends.

## Sync job failures

The nightly sync job did not finish.

1. Read the job log for the failed batch.
2. Rerun the job from that batch.
