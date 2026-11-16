# UC-02: account selection and synchronization

## Scope

Synthetic implementation only. Real Jack Henry mapping remains pending verified
access, entitlements, available/ledger/hold semantics, pagination, history-window
and transaction-lineage evidence. No guessed vendor endpoints are implemented.
UC-03, forecasts, payroll detection, and UC-18 reconnection are outside this branch.

UC-01 is the parent commit. Use the same Python 3.12 environment and requirements.
From the repository root in VS Code's terminal:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn flowshift.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Open http://127.0.0.1:8000. Stop an older demo server with Ctrl+C first if needed.
No credentials or environment changes are required. Keep the demo local.

## Demo

1. Review and grant the expanded synthetic-data consent. Old UC-01 sessions do
   not automatically acquire permission to read balances/history.
2. The account selector lists the account authorized by this consent. This slice
   deliberately supports one account per grant; choose another synthetic identity
   through the UC-01 flow to change business/account context.
3. Before synchronization, there is no snapshot and no invented zero balance.
4. Synchronize the pending scenario, then posted. The supplier economic event
   appears once despite provider ID changes and overlapping pages.
5. Retry posted: no duplicate transactions are added. An import version records
   each complete refresh even when event content is unchanged.
6. Try second-page failure, timeout, or unsupported currency. The prior complete
   snapshot/version survives, explicitly marked stale with a retry message. With
   no previous snapshot, balance remains unavailable.
7. Empty history is a successful complete import with no transactions; it is not
   a failed provider read. Switching fixture scenarios replaces the complete
   synthetic history, so the empty scenario intentionally clears its events.

## Internal contracts

- GET `/api/accounts`: authorized synthetic business checking account metadata.
- GET `/api/accounts/{id}/data`: versioned snapshot, normalized events, environment,
  sync status, last attempt, error and stale flag. Snapshot is null until a complete import.
- POST `/api/accounts/{id}/sync`: JSON `{"scenario":"posted"}`; also supports
  `pending`, `empty`, `partial`, `timeout`, `nonusd`. Requires same-origin JSON,
  session authorization and X-CSRF-Token from `/api/session`.

These are internal FlowShift paths. AccountProvider is an internal normalized
boundary, not a claim about vendor response schemas. A provider returns a balance
snapshot and pages of events with an explicit terminal cursor. Publication waits
for every page; repeated cursors and a 100-page bound fail closed. A verified real
adapter will need actual timeouts, bounded backoff/rate-limit handling, stable
provider snapshots, and sanitized contract fixtures. The synthetic adapter has
immediate failures and manual retry; it makes no bank refresh-performance claim.

All money is integer USD cents. Opening available balance is retained unchanged;
ledger and holds are separate fields. Synthetic history is already reflected in
that balance and is not subtracted again. Stable economic IDs reconcile pending
and posted provider IDs; posted events do not regress on pending replays. Real
lineage mappings cannot be assumed from merchant text alone. Synthetic timestamps
are UTC epoch seconds, with an IANA business timezone recorded separately.

SQLite uses the existing ignored `tmp/uc01.sqlite3` file with additive tables and a
consent-column migration. One transaction publishes the complete dataset and audit
record. Concurrent successful imports use a version check; a stale writer gets
409. Revocation/membership is rechecked before each page and inside publication.
Every read remains account-scoped; viewer sync refreshes data but grants no
business approval or financial mutation permission. Latest complete data is
stored; immutable historical snapshot storage for full UC-19 is future work.

Stale is true after failed refresh or when the provider as-of exceeds the proposed
15-minute engineering default. Failed imports never replace complete data. Error
details are generic and contain no provider payloads or tokens.

## Verification

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe -m pip check
```

Tests cover UC-01 regressions plus empty/uninitialized data, pagination overlap,
duplicate imports, pending/posting lineage, non-regression, failed/unsupported
imports, access removal during import, context-only consent, CSRF/account
isolation, and repeated cursors. Synthetic checks do not certify bank integration.
