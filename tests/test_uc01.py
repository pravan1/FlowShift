"""UC-01 / EC-08,09,44,45: synthetic boundary and authorization regression tests."""

import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

from flowshift.access import AccessStore, digest
from flowshift.main import create_app, SESSION_COOKIE, CONSENT_COOKIE


class AccessTests(unittest.TestCase):
    def setUp(self):
        Path("tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir="tmp")
        self.addCleanup(self.temp.cleanup)
        self.now = 1_800_000_000
        self.store = AccessStore(Path(self.temp.name) / "access.sqlite3", lambda: self.now)
        self.client = TestClient(create_app(self.store), base_url="http://127.0.0.1:8000")
        self.addCleanup(self.client.close)
        self.client.headers["Origin"] = "http://127.0.0.1:8000"

    def start(self, profile="bakery-owner"):
        response = self.client.post("/api/demo/start", json={"profile": profile})
        self.assertEqual(response.status_code, 200)
        return response.json()["challenge"]

    def login(self, profile="bakery-owner"):
        challenge = self.start(profile)
        response = self.client.post("/api/demo/consent", json={"challenge": challenge, "decision": "grant"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=strict", response.headers["set-cookie"])
        return self.client.get("/api/session").json()

    def test_explicit_consent_required_and_denial_blocks_access(self):
        token = self.start()
        self.assertEqual(self.client.get("/api/session").status_code, 403)
        response = self.client.post("/api/demo/consent", json={"challenge": token, "decision": "deny"})
        self.assertEqual(response.json()["status"], "denied")
        self.assertEqual(self.client.get("/api/session").status_code, 403)

    def test_context_is_server_scoped_and_synthetic(self):
        context = self.login()
        self.assertEqual(context["business_id"], "demo-bakery")
        self.assertEqual(context["environment"], "synthetic")
        self.assertNotIn("balance", context)
        self.assertEqual(self.client.get("/api/accounts/demo-checking-a/context").status_code, 200)
        other = self.client.get("/api/accounts/demo-checking-b/context")
        missing = self.client.get("/api/accounts/not-real/context")
        self.assertEqual((other.status_code, other.json()), (missing.status_code, missing.json()))

    def test_viewer_and_live_role_downgrade(self):
        self.login()
        raw = self.client.cookies.get(SESSION_COOKIE)
        self.store.authorize(raw, "owner:eligible")
        with self.store.connection() as db:
            db.execute("UPDATE memberships SET role='viewer' WHERE actor='demo-owner'")
        with self.assertRaises(HTTPException):
            self.store.authorize(raw, "owner:eligible")
        self.assertEqual(self.client.get("/api/session").json()["permissions"], ["context:read"])
        viewer = self.login("bakery-viewer")
        self.assertEqual(viewer["role"], "viewer")

    def test_removed_membership_blocks_existing_session(self):
        self.login()
        with self.store.connection() as db:
            db.execute("UPDATE memberships SET active=0 WHERE actor='demo-owner'")
        self.assertEqual(self.client.get("/api/session").status_code, 403)

    def test_session_expires_at_boundary(self):
        self.login()
        self.now += 1800
        self.assertEqual(self.client.get("/api/session").status_code, 403)

    def test_logout_and_revocation_reject_replayed_cookie(self):
        for operation in ("logout", "revoke-consent"):
            context = self.login()
            raw = self.client.cookies.get(SESSION_COOKIE)
            response = self.client.post(f"/api/session/{operation}", json={},
                                        headers={"X-CSRF-Token": context["csrf_token"]})
            self.assertEqual(response.status_code, 200)
            self.client.cookies.set(SESSION_COOKIE, raw)
            self.assertEqual(self.client.get("/api/session").status_code, 403)
            self.client.cookies.clear()

    def test_challenge_browser_binding_expiry_and_replay(self):
        token = self.start()
        self.assertEqual(self.client.post("/api/demo/consent", json={"challenge": "wrong", "decision": "grant"}).status_code, 403)
        self.now += 300
        self.assertEqual(self.client.post("/api/demo/consent", json={"challenge": token, "decision": "grant"}).status_code, 403)
        token = self.start()
        body = {"challenge": token, "decision": "grant"}
        self.assertEqual(self.client.post("/api/demo/consent", json=body).status_code, 200)
        self.client.cookies.set(CONSENT_COOKIE, token)
        self.assertEqual(self.client.post("/api/demo/consent", json=body).status_code, 403)

    def test_cross_origin_missing_origin_csrf_and_host_rejected(self):
        self.assertEqual(self.client.post("/api/demo/start", json={"profile": "bakery-owner"}, headers={"Origin": "https://evil.example"}).status_code, 403)
        del self.client.headers["Origin"]
        self.assertEqual(self.client.post("/api/demo/start", json={"profile": "bakery-owner"}).status_code, 403)
        self.client.headers["Origin"] = "http://127.0.0.1:8000"
        self.login()
        self.assertEqual(self.client.post("/api/session/logout", json={}).status_code, 403)
        self.assertEqual(self.client.get("/api/session", headers={"Host": "evil.example"}).status_code, 400)

    def test_switching_identity_invalidates_old_session(self):
        self.login()
        raw = self.client.cookies.get(SESSION_COOKIE)
        self.start("studio-owner")
        with self.assertRaises(HTTPException):
            self.store.authorize(raw)
        self.assertEqual(self.client.get("/api/session").status_code, 403)

    def test_session_persistence_audit_and_no_raw_tokens(self):
        self.login()
        token = self.client.cookies.get(SESSION_COOKIE)
        restored = AccessStore(self.store.path, lambda: self.now)
        self.assertEqual(restored.authorize(token)["role"], "owner")
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT token_hash FROM sessions").fetchone()[0], digest(token))
            events = db.execute("SELECT * FROM audit").fetchall()
            self.assertTrue(any(row["event"] == "consent.granted" for row in events))
            self.assertTrue(all(row["environment"] == "synthetic" for row in events))
            self.assertNotIn(token, str([tuple(row) for row in events]))

    def test_untrusted_context_fields_and_unknown_profiles_rejected(self):
        self.assertEqual(self.client.post("/api/demo/start", json={"profile": "bakery-owner", "role": "owner", "business_id": "other"}).status_code, 422)
        self.assertEqual(self.client.post("/api/demo/start", json={"profile": "unknown"}).status_code, 400)

    def test_demo_page_and_security_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("SYNTHETIC DEMO", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(self.client.get("/demo.js").status_code, 200)
        self.assertEqual(self.client.get("/demo.css").status_code, 200)


if __name__ == "__main__":
    Path("tmp").mkdir(exist_ok=True)
    unittest.main()
