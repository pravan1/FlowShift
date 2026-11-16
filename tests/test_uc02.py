"""UC-02 ingestion, isolation and failure regression checks."""
import unittest
from fastapi import HTTPException
from flowshift.accounts import AccountData, SyntheticProvider
import test_uc01


class AccountTests(unittest.TestCase):
    start = test_uc01.AccessTests.start
    login = test_uc01.AccessTests.login

    def setUp(self):
        test_uc01.AccessTests.setUp(self)
        self.context = self.login()
        self.token = self.client.cookies.get("flowshift_demo_session")
        self.data = AccountData(self.store)
        self.account = self.context["account_id"]

    def sync(self, scenario="posted"):
        return self.data.sync(self.token, self.account, SyntheticProvider(lambda: self.now, scenario))

    def test_uc02_no_invented_balance_before_sync(self):
        self.assertIsNone(self.data.read(self.token, self.account)["snapshot"])

    def test_uc02_pages_duplicates_and_pending_lineage(self):
        first = self.sync("pending")
        self.assertEqual(len(first["events"]), 2)
        posted = self.sync()
        self.assertEqual(len(posted["events"]), 2)
        supplier = next(e for e in posted["events"] if e["economic_id"] == "supply-1")
        self.assertEqual(supplier["status"], "posted")
        self.assertEqual(self.sync()["events"], posted["events"])
        self.assertEqual(posted["snapshot"]["available_cents"], 840000)

    def test_uc02_failures_preserve_complete_snapshot(self):
        original = self.sync()
        for scenario in ("partial", "timeout", "nonusd"):
            failed = self.sync(scenario)
            self.assertEqual(failed["snapshot"], original["snapshot"])
            self.assertEqual(failed["version"], original["version"])
            self.assertTrue(failed["stale"])
        self.assertFalse(self.sync()["stale"])

    def test_uc02_empty_is_complete_not_failure(self):
        result = self.sync("empty")
        self.assertEqual(result["events"], [])
        self.assertEqual(result["sync_status"], "complete")

    def test_uc02_posted_does_not_regress_to_pending(self):
        self.sync()
        result = self.sync("pending")
        self.assertEqual(next(e for e in result["events"] if e["economic_id"] == "supply-1")["status"], "posted")

    def test_uc02_old_context_only_consent_cannot_read_data(self):
        with self.store.connection() as db:
            db.execute("UPDATE sessions SET data_consent=0")
        with self.assertRaises(HTTPException):
            self.data.read(self.token, self.account)

    def test_uc02_repeated_cursor_fails_closed(self):
        class BrokenProvider(SyntheticProvider):
            def page(self, account, cursor):
                return dict(events=[], next_cursor="same")
        result = self.data.sync(self.token, self.account, BrokenProvider(lambda:self.now))
        self.assertIsNone(result["snapshot"])
        self.assertEqual(result["sync_status"], "failed")

    def test_uc02_revocation_mid_import_prevents_publication(self):
        store, token = self.store, self.token
        class RevokingProvider(SyntheticProvider):
            def page(self, account, cursor):
                result = super().page(account,cursor)
                store.revoke(token, "test.revoked")
                return result
        with self.assertRaises(HTTPException):
            self.data.sync(token,self.account,RevokingProvider(lambda:self.now))
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM account_data").fetchone()[0],0)

    def test_uc02_routes_auth_csrf_and_isolation(self):
        self.assertEqual(self.client.get("/api/accounts").status_code,200)
        url=f"/api/accounts/{self.account}/sync"
        self.assertEqual(self.client.post(url,json={}).status_code,403)
        response=self.client.post(url,json={},headers={"X-CSRF-Token":self.context["csrf_token"]})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get("/api/accounts/demo-checking-b/data").status_code,403)


if __name__ == "__main__":
    unittest.main()
