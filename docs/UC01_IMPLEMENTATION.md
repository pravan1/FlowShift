# UC-01: authorized access (synthetic implementation)

## Scope and status

Implemented: local synthetic consent, scoped account identity, owner/viewer
authorization, expiry, logout, consent revocation, and append-only access audit
records. This is not a Jack Henry integration or bank login. No bank credentials
are collected. No balances, transaction ingestion, obligations, forecasts,
approvals, payment simulation, or UC-18 reconnection workflow are implemented.

Acceptance mapping: UC-01; applicable EC-08 (revoked/expired access), EC-09
(context replacement), EC-44 (tenant isolation and role downgrade), EC-45
(synthetic mode). Real bank/sandbox authentication evidence remains BLOCKED
pending access and actual institution entitlements.

## Local setup (PowerShell, repository root)

Use Python 3.12. The app was tested with that version. If `python` opens the
Microsoft Store, use an installed Python interpreter or `py -3.12` instead.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn flowshift.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Open http://127.0.0.1:8000. Stop with Ctrl+C. No environment variables or bank
secrets are required. Optional `FLOWSHIFT_MODE` accepts only `synthetic`; another
value refuses startup. Do not expose this identity-selector demo through a
tunnel, reverse proxy, shared server, or public deployment.

Run tests:

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe -m pip check
```

The installed Starlette version emits a deprecation warning for its supported
httpx test-client fallback; this does not indicate a failing test. Windows async
test execution requires local loopback sockets, even for in-process TestClient.

## Try the use case

1. Choose the bakery owner. No access is granted by selecting an identity.
2. Review requested access. Deny it: no session/account access is granted.
3. Review again and allow. The page shows institution, business, account, role,
   permissions, expiry, and SYNTHETIC mode.
4. Log out or revoke consent. Further access, including replay of the old
   session cookie, is rejected.
5. Choose the bakery viewer. It has only `context:read` permission.
6. Start entry as the studio owner. The old session is revoked before consent
   to the new identity; no old account context remains authorized.

Thirty-minute session expiry and five-minute consent-request expiry are
engineering defaults for this local demonstration, not bank-approved policies.

## Internal API contract

All paths below are FlowShift paths, not vendor endpoints. Request bodies are
JSON. POST requests require an Origin matching the exact local server origin.
No cross-origin access is configured. Errors use HTTP 400/403/415/422 as
appropriate; account-not-found and account-not-authorized have the same 403
response to avoid revealing account existence.

| Method / path | Contract |
| --- | --- |
| GET `/api/demo/profiles` | Public fictional identity/account labels, always synthetic |
| POST `/api/demo/start` | `{ "profile": "bakery-owner" }`; creates browser-bound consent challenge, replaces existing session |
| POST `/api/demo/consent` | `{ "challenge": "<returned challenge>", "decision": "grant" }` or `deny`; single-use challenge and matching cookie required |
| GET `/api/session` | Authorized context, current role/permissions, expiry, CSRF token, correlation ID |
| GET `/api/accounts/{account_id}/context` | Same context only when account matches the server-owned session and active membership |
| POST `/api/session/logout` | `{}` plus `X-CSRF-Token` from session response; invalidates server session |
| POST `/api/session/revoke-consent` | `{}` plus `X-CSRF-Token`; revokes this synthetic consent/session |

Session context example (dynamic fields abbreviated):

```json
{
  "actor_id": "demo-owner",
  "institution_id": "demo-institution-a",
  "business_id": "demo-bakery",
  "account_id": "demo-checking-a",
  "role": "owner",
  "permissions": ["context:read", "owner:eligible"],
  "environment": "synthetic",
  "expires_at": 1800001800,
  "csrf_token": "<per-session token>",
  "correlation_id": "<access audit correlation>"
}
```

`owner:eligible` is only a synthetic role eligibility marker. It authorizes no
payment or other implemented mutation. Future mutation endpoints must check
their own verified permission, consent, freshness, and exact resource context.
They must not trust a cached role or client-supplied business/account IDs.

`AccessStore.authorize(token, permission, account)` is the reusable server-side
guard. It checks current membership and role every time, not only at login.
Do not allow workers to bypass this check when their future work requires a
current user grant. No worker or action endpoint exists in this slice.

## Storage and security decisions

- One modular FastAPI app, standard-library SQLite, and plain HTML/CSS/JS:
  minimum dependencies for UC-01; this does not prescribe the team's final UI.
- SQLite is stored at ignored `tmp/uc01.sqlite3`. It persists sessions and audit
  records across restarts. Never commit it; it contains ephemeral local tokens
  and synthetic access metadata. Membership rows are seeded once, not reset on
  each request/restart. New database fixtures are all fictional.
- Opaque session/challenge tokens are generated with `secrets`; only hashes
  are persisted. CSRF tokens remain server-side and are returned only to an
  authorized same-origin session. No browser localStorage is used.
- Cookies are HttpOnly and SameSite=Strict. Secure is deliberately unset for
  HTTP on loopback only. Real hosting requires HTTPS and Secure cookies plus a
  separately reviewed authentication implementation.
- Exact Origin checks, JSON-only mutation bodies, browser-bound single-use
  consent challenges, CSRF checks for authenticated mutations, local-client and
  trusted-host restrictions, CSP, and no-store responses apply to the demo.
- Consent consumption and session creation share one transaction. Audit writes
  participate in the same transaction; a persistence failure does not return a
  successfully created session. SQLite connections are closed after use.
- Audit events carry synthetic environment and correlation IDs, never raw
  session/challenge/CSRF tokens. There is no public audit-browser endpoint.
- This local fixture is not a production identity provider. Identity selection
  is intentionally available to the person operating the local demo. Support
  and worker identities, external OAuth callbacks, OIDC/PKCE validation, bank
  scopes, and provider token refresh are not fabricated here.

## Verification and remaining work

Automated tests cover consent denial, challenge mismatch/expiry/replay, session
expiry, logout/revocation replay, cross-business denial, current role downgrade,
membership removal, identity replacement, CSRF/Origin/Host checks, persistence,
audit redaction, explicit mode, and client-supplied context rejection.

Browser checks exercised consent denial, owner entry, consent revocation, and
viewer entry. The authorized-context page was visually inspected at the default
viewport and 360px width, with no horizontal overflow. Browser checks supplement
the automated tests; they do not validate actual bank login or Banno embedding.

Before marking real UC-01 integration complete: follow JACK_HENRY.md to verify
the actual OAuth/OIDC client flow, callback state/nonce and PKCE where required,
issuer/audience/signature validation, least-privilege scopes, business-account
entitlements, denied consent, provider expiry/revocation, and host integration.
Real provider evidence must be distinguished from passing synthetic tests.
