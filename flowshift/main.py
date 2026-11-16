"""Local-only synthetic UC-01 app. No OAuth endpoints or payment capability."""

import os
import secrets
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from flowshift.access import AccessStore, PROFILES
from flowshift.accounts import AccountData, SyntheticProvider

ROOT = Path(__file__).resolve().parent
SESSION_COOKIE = "flowshift_demo_session"
CONSENT_COOKIE = "flowshift_demo_consent"


class StartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str


class ConsentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    challenge: str
    decision: Literal["grant", "deny"]


class SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Literal["posted", "pending", "empty", "partial", "timeout", "nonusd"] = "posted"


def create_app(store=None):
    if os.environ.get("FLOWSHIFT_MODE", "synthetic") != "synthetic":
        raise RuntimeError("UC-01 supports synthetic mode only; no bank adapter is installed")
    app = FastAPI(title="FlowShift UC-01 — synthetic access demo", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
    app.state.access = store or AccessStore(Path("tmp/uc01.sqlite3"))
    app.state.accounts = AccountData(app.state.access)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        # No remotely reachable demo identity selector or cross-origin mutations.
        if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            return Response("Local synthetic demo only", status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if request.headers.get("origin") != expected:
                return Response("Same-origin request required", status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return Response("JSON required", status_code=415)
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response

    def access(request: Request):
        return app.state.access.authorize(request.cookies.get(SESSION_COOKIE))

    def mutation(request: Request, context=Depends(access)):
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), context["csrf_token"]):
            raise HTTPException(403, "CSRF token invalid")
        return context

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "static/index.html")

    @app.get("/demo.js", include_in_schema=False)
    def script():
        return FileResponse(ROOT / "static/demo.js", media_type="text/javascript")

    @app.get("/demo.css", include_in_schema=False)
    def styles():
        return FileResponse(ROOT / "static/demo.css", media_type="text/css")

    @app.get("/api/demo/profiles")
    def profiles():
        return {"environment": "synthetic", "profiles": [
            {"id": key, "business_id": value[2], "account_id": value[3], "role": value[4]}
            for key, value in PROFILES.items()
        ]}

    @app.post("/api/demo/start")
    def start(body: StartInput, request: Request, response: Response):
        token = app.state.access.start(body.profile)
        previous = request.cookies.get(SESSION_COOKIE)
        if previous:
            app.state.access.revoke(previous, "session.replaced")
        response.delete_cookie(SESSION_COOKIE)
        response.set_cookie(CONSENT_COOKIE, token, httponly=True, samesite="strict", max_age=300)
        return {"environment": "synthetic", "challenge": token,
                "requested_access": ["Synthetic account context, balances, and transaction history"],
                "notice": "Synthetic data only; no bank connection or money movement."}

    @app.post("/api/demo/consent")
    def consent(body: ConsentInput, request: Request, response: Response):
        browser_token = request.cookies.get(CONSENT_COOKIE, "")
        if not browser_token or not secrets.compare_digest(browser_token, body.challenge):
            raise HTTPException(403, "Consent request does not match this browser")
        result = app.state.access.consent(body.challenge, body.decision == "grant")
        response.delete_cookie(CONSENT_COOKIE)
        if result["status"] == "granted":
            from flowshift.access import digest
            with app.state.access.connection() as db:
                db.execute("UPDATE sessions SET data_consent=1 WHERE token_hash=?", (digest(result["session"]),))
                app.state.access.audit(db, "account_data.consent_granted")
            response.set_cookie(SESSION_COOKIE, result["session"], httponly=True, samesite="strict", max_age=1800)
        return {"environment": "synthetic", "status": result["status"]}

    @app.get("/api/session")
    def session(context=Depends(access)):
        return context

    @app.get("/api/accounts/{account_id}/context")
    def account_context(account_id: str, request: Request):
        return app.state.access.authorize(request.cookies.get(SESSION_COOKIE), account=account_id)

    @app.get("/api/accounts")
    def accounts(context=Depends(access)):
        return {"environment": "synthetic", "accounts": [{"account_id": context["account_id"],
                "business_id": context["business_id"], "currency": "USD", "type": "business_checking"}]}

    @app.get("/api/accounts/{account_id}/data")
    def data(account_id: str, request: Request):
        return app.state.accounts.read(request.cookies.get(SESSION_COOKIE), account_id)

    @app.post("/api/accounts/{account_id}/sync")
    def sync(account_id: str, body: SyncInput, request: Request, context=Depends(mutation)):
        return app.state.accounts.sync(request.cookies.get(SESSION_COOKIE), account_id,
                                       SyntheticProvider(app.state.access.clock, body.scenario))

    @app.post("/api/session/logout")
    def logout(request: Request, response: Response, context=Depends(mutation)):
        app.state.access.revoke(request.cookies[SESSION_COOKIE], "session.logout")
        response.delete_cookie(SESSION_COOKIE)
        return {"environment": "synthetic", "status": "logged_out"}

    @app.post("/api/session/revoke-consent")
    def revoke(request: Request, response: Response, context=Depends(mutation)):
        app.state.access.revoke(request.cookies[SESSION_COOKIE], "consent.revoked")
        response.delete_cookie(SESSION_COOKIE)
        return {"environment": "synthetic", "status": "revoked"}

    return app
