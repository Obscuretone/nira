"""Owner sign-in with Nira's configured OpenID Connect provider."""

import re
import time
from urllib.parse import urlsplit

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from fastapi.responses import RedirectResponse
from httpx2 import HTTPError
from joserfc.errors import JoseError
from starlette.middleware.sessions import SessionMiddleware

from .browse import browse_login
from .store import DomainError


MAX_AGE = 300
RECORD_PATH = re.compile(r"/browse/(?:project|issue)/[A-Za-z0-9_-]{1,80}")


def safe_destination(prefix, target):
    if isinstance(target, str) and target.startswith(prefix):
        path = target[len(prefix):]
        if RECORD_PATH.fullmatch(path):
            return target
    return prefix + "/"


def register_passkeys(app, config, origin, prefix, issue_session):
    issuer = config["issuer"]
    parsed = urlsplit(issuer)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or issuer == origin):
        raise ValueError("Configure a separate HTTPS OIDC issuer.")
    client_id, secret, subject, session_key = (config[k] for k in (
        "client_id", "client_secret", "owner_subject", "session_key"
    ))
    if not all((client_id, secret, subject)) or len(session_key) < 32:
        raise ValueError("Configure the Nira OIDC client, owner and session key.")
    app.add_middleware(SessionMiddleware, secret_key=session_key, session_cookie="__Host-nira-oidc",
                       max_age=MAX_AGE, same_site="lax", https_only=True)
    client = OAuth().register(
        "nira_identity", client_id=client_id, client_secret=secret,
        server_metadata_url=issuer + "/.well-known/openid-configuration",
        client_kwargs={"scope": "openid", "code_challenge_method": "S256", "timeout": 6},
    )
    callback = origin + prefix + "/auth/callback"

    @app.get("/auth/sign-in")
    async def passkey_signin(request: Request, next: str = ""):
        request.session.clear()
        target = safe_destination(prefix, next)
        request.session["nira_destination"] = target
        try:
            if (await client.load_server_metadata()).get("issuer") != issuer:
                raise ValueError("Unexpected identity issuer.")
            return await client.authorize_redirect(request, callback, max_age=MAX_AGE)
        except (OAuthError, HTTPError, ValueError):
            request.session.clear()
            return browse_login(prefix, target, True, "Passkey sign-in is temporarily unavailable.", 503)

    @app.get("/auth/callback")
    async def passkey_callback(request: Request):
        target = safe_destination(prefix, request.session.get("nira_destination"))
        try:
            token = await client.authorize_access_token(
                request,
                claims_options={"iss": {"essential": True, "value": issuer},
                                "auth_time": {"essential": True}},
                leeway=30,
            )
        except (OAuthError, JoseError, HTTPError, ValueError):
            return browse_login(prefix, target, True, "Sign-in failed. Try again from Nira.", 401)
        finally:
            request.session.clear()
        claims = token.get("userinfo") or {}
        auth_time = claims.get("auth_time")
        now = int(time.time())
        if (claims.get("iss") != issuer or claims.get("sub") != subject or type(auth_time) is not int
                or not now - MAX_AGE - 30 <= auth_time <= now + 30):
            return browse_login(prefix, target, True, "This workspace requires a recent owner sign-in.", 403)
        return issue_session(request, RedirectResponse(target, status_code=303))
