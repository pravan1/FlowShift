"""Server-owned identity, consent and account authorization. No bank integration."""

import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException


PROFILES = {
    "bakery-owner": ("demo-owner", "demo-institution-a", "demo-bakery", "demo-checking-a", "owner"),
    "bakery-viewer": ("demo-viewer", "demo-institution-a", "demo-bakery", "demo-checking-a", "viewer"),
    "studio-owner": ("demo-studio-owner", "demo-institution-b", "demo-studio", "demo-checking-b", "owner"),
}
PERMISSIONS = {"owner": ("context:read", "owner:eligible"), "viewer": ("context:read",)}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AccessStore:
    """Local SQLite persistence; raw session/challenge tokens are never stored."""

    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = path, clock
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS memberships (
                    actor TEXT, institution TEXT, business TEXT, account TEXT,
                    role TEXT, active INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(actor, account));
                CREATE TABLE IF NOT EXISTS challenges (
                    token_hash TEXT PRIMARY KEY, profile TEXT, expires INTEGER,
                    consumed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, actor TEXT, institution TEXT,
                    business TEXT, account TEXT, csrf TEXT, expires INTEGER,
                    revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY, at INTEGER, correlation_id TEXT,
                    event TEXT, actor TEXT, account TEXT, outcome TEXT,
                    environment TEXT NOT NULL DEFAULT 'synthetic');
            """)
            for profile in PROFILES.values():
                db.execute("INSERT OR IGNORE INTO memberships VALUES (?, ?, ?, ?, ?, 1)", profile)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    def audit(self, db, event, actor=None, account=None, outcome="allowed"):
        correlation = secrets.token_hex(12)
        db.execute(
            "INSERT INTO audit(at,correlation_id,event,actor,account,outcome) VALUES(?,?,?,?,?,?)",
            (int(self.clock()), correlation, event, actor, account, outcome),
        )
        return correlation

    def start(self, profile):
        if profile not in PROFILES:
            raise HTTPException(400, "Unknown synthetic profile")
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute("DELETE FROM challenges WHERE expires <= ?", (int(self.clock()),))
            db.execute("INSERT INTO challenges VALUES (?, ?, ?, 0)",
                       (digest(token), profile, int(self.clock()) + 300))
            self.audit(db, "consent.started", PROFILES[profile][0], PROFILES[profile][3])
        return token

    def consent(self, token, granted):
        result = None
        with self.connection() as db:
            # Serialize consumption: two concurrent requests cannot redeem one challenge.
            db.execute("BEGIN IMMEDIATE")
            challenge = db.execute("SELECT * FROM challenges WHERE token_hash=?", (digest(token),)).fetchone()
            if not challenge or challenge["consumed"] or challenge["expires"] <= self.clock():
                self.audit(db, "consent.invalid", outcome="denied")
            else:
                db.execute("UPDATE challenges SET consumed=1 WHERE token_hash=?", (digest(token),))
                actor, institution, business, account, _ = PROFILES[challenge["profile"]]
                if not granted:
                    self.audit(db, "consent.denied", actor, account, "denied")
                    result = {"status": "denied"}
                else:
                    membership = db.execute("SELECT * FROM memberships WHERE actor=? AND account=?",
                                            (actor, account)).fetchone()
                    if membership and membership["active"] and membership["role"] in PERMISSIONS:
                        session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                        db.execute("INSERT INTO sessions(token_hash,actor,institution,business,account,csrf,expires,revoked) VALUES(?,?,?,?,?,?,?,0)",
                                   (digest(session), actor, institution, business, account, csrf, int(self.clock()) + 1800))
                        self.audit(db, "consent.granted", actor, account)
                        result = {"status": "granted", "session": session}
                    else:
                        self.audit(db, "consent.unauthorized", actor, account, "denied")
        if result is None:
            raise HTTPException(403, "Consent request invalid, expired, or unavailable")
        return result

    def authorize(self, token, permission="context:read", account=None):
        context = None
        with self.connection() as db:
            row = db.execute("SELECT * FROM sessions WHERE token_hash=?", (digest(token or ""),)).fetchone()
            if row and not row["revoked"] and row["expires"] > self.clock():
                membership = db.execute("SELECT * FROM memberships WHERE actor=? AND account=?",
                                        (row["actor"], row["account"])).fetchone()
                if (membership and membership["active"]
                        and membership["institution"] == row["institution"]
                        and membership["business"] == row["business"]
                        and (account is None or account == row["account"])
                        and permission in PERMISSIONS.get(membership["role"], ())):
                    context = {
                        "actor_id": row["actor"], "institution_id": row["institution"],
                        "business_id": row["business"], "account_id": row["account"],
                        "role": membership["role"], "permissions": list(PERMISSIONS[membership["role"]]),
                        "environment": "synthetic", "expires_at": row["expires"], "csrf_token": row["csrf"],
                    }
            correlation = self.audit(db, "context.checked", row["actor"] if row else None,
                                     row["account"] if row else None, "allowed" if context else "denied")
        if context is None:
            # Same response for nonexistent and unauthorized account IDs.
            raise HTTPException(403, "Access unavailable; enter again with consent")
        context["correlation_id"] = correlation
        return context

    def revoke(self, token, event):
        with self.connection() as db:
            row = db.execute("SELECT * FROM sessions WHERE token_hash=?", (digest(token),)).fetchone()
            db.execute("UPDATE sessions SET revoked=1 WHERE token_hash=?", (digest(token),))
            self.audit(db, event, row["actor"] if row else None, row["account"] if row else None)
