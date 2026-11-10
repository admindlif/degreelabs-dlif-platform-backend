# Backend security operations

## Authentication rate limiting

Authentication failures are throttled with PostgreSQL-backed buckets so the
limits are shared by every API worker. Edge limits should remain enabled as an
additional denial-of-service control.

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

Database rate-limit keys are HMAC digests. Raw email addresses, tokens, and IP
addresses are not stored in the rate-limit table.

## Secret handling

Configure database, JWT, SMTP, and Google credentials through the deployment
secret store. Never place credential values in source control or application
logs. A previously exposed Google credential is compromised and must be revoked
and replaced in Google Cloud before production use.

## Production configuration

Set `CORS_ALLOWED_ORIGINS` to a comma-separated list of exact HTTP(S) origins.
Development also permits the local Fellow, Admin, and Mentor ports 3000-3003;
production rejects localhost origins and requires the SMTP backend. Startup
validation also rejects non-positive token expiries, incomplete SMTP
credentials, and enabled Google integrations whose required credential-file
settings are absent.

### Authenticated session policy

The server-managed session defaults are:

- `ACCESS_TOKEN_EXPIRE_MINUTES=15`
- `SESSION_INACTIVITY_TIMEOUT_MINUTES=1440`
- `SESSION_WARNING_MINUTES=5`
- `SESSION_ABSOLUTE_FELLOW_HOURS=168`
- `SESSION_ABSOLUTE_MENTOR_HOURS=72`
- `SESSION_ABSOLUTE_PRIVILEGED_HOURS=24`
- `SESSION_REFRESH_TOKEN_EXPIRE_HOURS=24`
- `SESSION_REFRESH_TOKEN_BYTES=48`
- `SESSION_REFRESH_RACE_GRACE_SECONDS=10`
- `SESSION_ACTIVITY_WRITE_INTERVAL_SECONDS=300`

Shared authentication throttle defaults are:

- `AUTH_LOGIN_ATTEMPT_LIMIT=5`, `AUTH_LOGIN_WINDOW_MINUTES=15`
- `AUTH_TWO_FA_ATTEMPT_LIMIT=5`, `AUTH_TWO_FA_WINDOW_MINUTES=10`
- `AUTH_RECOVERY_ATTEMPT_LIMIT=10`, `AUTH_RECOVERY_WINDOW_MINUTES=15`
- `AUTH_REFRESH_ATTEMPT_LIMIT=30`, `AUTH_REFRESH_WINDOW_MINUTES=5`

The approved portal origins should be configured with:

- `FRONTEND_BASE_URL=https://fellow.degreelabs.com`
- `MENTOR_FRONTEND_BASE_URL=https://mentor.degreelabs.com`
- `ADMIN_FRONTEND_BASE_URL=https://admin.degreelabs.com`
- `CORS_ALLOWED_ORIGINS=https://fellow.degreelabs.com,https://mentor.degreelabs.com,https://admin.degreelabs.com`

Rollout controls:

- `ALLOW_LEGACY_SESSIONLESS_ACCESS_TOKENS=true` permits only already-issued
  sessionless access JWTs during the bounded migration window. Set it to
  `false` after every old access token has naturally expired.
- `PRIVILEGED_MFA_ENFORCEMENT_ENABLED=false` keeps existing privileged users
  from being locked out before the enrollment UI is deployed. Set it to
  `true` only after Stage 2 supports `requires_2fa_setup` and the existing-user
  enrollment flow.

Unsafe requests carrying an authentication cookie must include an exact
approved `Origin`. Non-browser Bearer clients without authentication cookies
remain compatible. Keep `AUTH_COOKIE_DOMAIN` unset unless a reviewed deployment
requires a parent-domain cookie; host-only API cookies reduce subdomain risk.
