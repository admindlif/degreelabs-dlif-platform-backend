# Backend security operations

## Edge rate limiting

The API does not use an in-process rate limiter. A per-process memory limiter
would provide inconsistent protection once the service runs with multiple
workers or replicas, so rate limiting must be enforced by the production API
gateway, ingress, load balancer, or reverse proxy.

Apply IP-based limits to these public authentication endpoints at minimum:

- `POST /api/v1/auth/login`
- `POST /api/v1/auth/2fa/verify`
- `POST /api/v1/auth/activate`
- `POST /api/v1/auth/onboarding/resume`

Use a strict burst limit for password, invitation-token, TOTP, and recovery-code
attempts. Keep the authenticated admin invitation endpoints behind the normal
admin authorization controls and add an account/admin-scoped delivery limit if
the edge supports claims-aware policies.

Rate-limit keys must use the trusted client address supplied by the platform.
Only honor forwarded-address headers from known proxies; otherwise clients can
spoof the key. Return HTTP 429 with a `Retry-After` header when a limit is hit.

## Secret handling

Configure database, JWT, SMTP, and Google credentials through the deployment
secret store. Never place credential values in source control or application
logs. A previously exposed Google credential is compromised and must be revoked
and replaced in Google Cloud before production use.

## Production configuration

Set `CORS_ALLOWED_ORIGINS` to a comma-separated list of exact HTTP(S) origins.
Development also permits `http://localhost:3000` and
`http://localhost:3002`; production rejects localhost origins and requires the
SMTP backend. Startup validation also rejects non-positive token expiries,
incomplete SMTP credentials, and enabled Google integrations whose required
credential-file settings are absent.
