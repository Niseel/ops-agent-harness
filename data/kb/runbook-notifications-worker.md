# notifications-worker runbook

notifications-worker sends email and push messages from the notifications queue.

## Queue backlog

More than 10000 messages wait in the queue.

1. Check that all workers are running.
2. Scale the workers out.

## Email bounces

The bounce rate is above 5 percent. Pause the campaign and clean the address list.

## Dead letters

Messages that failed three times go to the dead letter queue. Fix the cause, then replay them.
