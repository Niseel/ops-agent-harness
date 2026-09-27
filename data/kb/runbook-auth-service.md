# auth-service runbook

## Login failures

Users cannot sign in. The logs show ECONNRESET from the identity provider.

1. Restart the identity provider connector.
2. If logins still fail, fail over to the second region.

## Token errors

Tokens are rejected as invalid.

- An expired signing key: rotate the key.
- Clock skew between hosts: resync NTP.

## Rate limits

Clients get HTTP 429 when they send more than 100 requests per second. Raise the limit only for known internal clients.
