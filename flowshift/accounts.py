"""UC-02: normalized account reads, atomic imports, and synthetic fixtures."""
import json
from typing import Protocol
from fastapi import HTTPException
from flowshift.access import digest


class AccountProvider(Protocol):
    def balance(self, account: str) -> dict: ...
    def page(self, account: str, cursor: str | None) -> dict: ...


class SyntheticProvider:
    def __init__(self, clock, scenario="posted"):
        self.clock, self.scenario = clock, scenario

    def balance(self, account):
        if self.scenario == "timeout":
            raise TimeoutError()
        return dict(currency="EUR" if self.scenario == "nonusd" else "USD",
                    available_cents=840000, ledger_cents=858000, holds_cents=18000,
                    as_of=int(self.clock()), balance_type="available", timezone="America/Chicago")

    def page(self, account, cursor):
        if self.scenario == "partial" and cursor:
            raise TimeoutError()
        pending = dict(provider_id="pending-supply", economic_id="supply-1", amount_cents=-18000,
                       status="pending", description="Synthetic supplier", effective_at=int(self.clock())-86400,
                       reflected_in_available=True, revision=1)
        posted = dict(pending, provider_id="posted-supply", status="posted", revision=2)
        credit = dict(provider_id="credit-1", economic_id="credit-1", amount_cents=100000,
                      status="posted", description="Synthetic settlement", effective_at=int(self.clock())-172800,
                      reflected_in_available=True, revision=1)
        if self.scenario == "empty":
            return dict(events=[], next_cursor=None)
        return dict(events=[pending, credit] if cursor is None else
                    [credit, pending if self.scenario == "pending" else posted],
                    next_cursor="page-2" if cursor is None else None)


class AccountData:
    def __init__(self, access):
        self.access = access
        with access.connection() as db:
            if "data_consent" not in {row[1] for row in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN data_consent INTEGER NOT NULL DEFAULT 0")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS account_data (
                    account TEXT PRIMARY KEY, version INTEGER NOT NULL, snapshot TEXT NOT NULL, events TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sync_status (
                    account TEXT PRIMARY KEY, status TEXT, attempted_at INTEGER, error TEXT);
            """)

    def read(self, token, account):
        context = self.access.authorize(token, account=account)
        with self.access.connection() as db:
            consent = db.execute("SELECT data_consent FROM sessions WHERE token_hash=?", (digest(token),)).fetchone()
            if not consent or not consent[0]:
                raise HTTPException(403, "Review consent for synthetic balances and history")
            row = db.execute("SELECT * FROM account_data WHERE account=?", (account,)).fetchone()
            status = db.execute("SELECT * FROM sync_status WHERE account=?", (account,)).fetchone()
        snapshot = json.loads(row["snapshot"]) if row else None
        return dict(account_id=account, business_id=context["business_id"], environment="synthetic",
                    version=row["version"] if row else None, snapshot=snapshot,
                    events=json.loads(row["events"]) if row else [],
                    sync_status=status["status"] if status else "never_synced",
                    last_attempt_at=status["attempted_at"] if status else None,
                    error=status["error"] if status else None,
                    stale=bool(row and ((status and status["status"] != "complete") or
                                        self.access.clock()-snapshot["as_of"] > 900)))

    def sync(self, token, account, provider: AccountProvider):
        original = self.read(token, account)
        try:
            snapshot = provider.balance(account)
            if snapshot["currency"] != "USD":
                raise ValueError("Unsupported currency")
            for field in ("available_cents", "ledger_cents", "holds_cents", "as_of"):
                if type(snapshot[field]) is not int:
                    raise ValueError("Invalid cents/timestamp")
            events, seen, cursor = {}, set(), None
            for _ in range(100):
                self.access.authorize(token, account=account)
                page = provider.page(account, cursor)
                for event in page["events"]:
                    if (type(event["amount_cents"]) is not int or event["status"] not in {"pending", "posted"}
                            or type(event["revision"]) is not int):
                        raise ValueError("Invalid event")
                    old = events.get(event["economic_id"])
                    rank = lambda e: (e["status"] == "posted", e["revision"])
                    if old is None or rank(event) > rank(old):
                        events[event["economic_id"]] = event
                    elif rank(event) == rank(old) and event != old:
                        raise ValueError("Conflicting events")
                cursor = page["next_cursor"]
                if cursor is None:
                    break
                if cursor in seen:
                    raise ValueError("Repeated cursor")
                seen.add(cursor)
            else:
                raise ValueError("Page limit")
            for old in original["events"]:
                new = events.get(old["economic_id"])
                if new and old["status"] == "posted" and new["status"] == "pending":
                    events[old["economic_id"]] = old
        except (TimeoutError, ValueError, KeyError, TypeError):
            self.access.authorize(token, account=account)
            with self.access.connection() as db:
                db.execute("INSERT OR REPLACE INTO sync_status VALUES(?,?,?,?)",
                           (account, "failed", int(self.access.clock()), "Incomplete or unsupported import; retry sync"))
                self.access.audit(db, "sync.failed", account=account, outcome="failed")
            return self.read(token, account)
        context = self.access.authorize(token, account=account)
        with self.access.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            grant = db.execute("""SELECT s.* FROM sessions s JOIN memberships m ON
                s.actor=m.actor AND s.account=m.account AND s.business=m.business AND s.institution=m.institution
                WHERE s.token_hash=? AND s.revoked=0 AND s.expires>? AND s.data_consent=1 AND m.active=1
                AND m.role IN ('owner','viewer') AND s.account=?""", (digest(token), self.access.clock(), account)).fetchone()
            if not grant:
                raise HTTPException(403, "Access unavailable; enter again with consent")
            current = db.execute("SELECT version FROM account_data WHERE account=?", (account,)).fetchone()
            if (current[0] if current else None) != original["version"]:
                raise HTTPException(409, "Newer sync completed; reload before retrying")
            snapshot.update(environment="synthetic", complete=True, ingested_at=int(self.access.clock()))
            db.execute("INSERT OR REPLACE INTO account_data VALUES(?,?,?,?)",
                       (account, (original["version"] or 0)+1, json.dumps(snapshot),
                        json.dumps(sorted(events.values(), key=lambda e: (e["effective_at"], e["economic_id"])))))
            db.execute("INSERT OR REPLACE INTO sync_status VALUES(?,?,?,NULL)", (account, "complete", int(self.access.clock())))
            self.access.audit(db, "sync.completed", context["actor_id"], account)
        return self.read(token, account)
