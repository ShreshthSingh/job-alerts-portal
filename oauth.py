"""GitHub OAuth2 (authorization code flow) helpers.

GitHub's OAuth apps are not OIDC-compliant (no id_token / discovery
document), so Streamlit's built-in `st.login()` can't be used. This is a
small manual implementation of the same flow.
"""

import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode

import requests

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"

SCOPE = "repo"


class OAuthError(RuntimeError):
    pass


def make_signed_state(secret: str) -> str:
    """A self-verifying CSRF token.

    Streamlit resets st.session_state on a full page reload, and GitHub's
    redirect back to us *is* a full page reload - so there's nothing
    reliable to compare the returned state against server-side. Signing it
    means it verifies itself on return instead of needing to be remembered.
    """
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(12)
    payload = f"{timestamp}.{nonce}"
    signature = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_signed_state(secret: str, state: str, max_age_seconds: int = 600) -> bool:
    try:
        timestamp_str, nonce, signature = state.split(".")
    except (ValueError, AttributeError):
        return False

    payload = f"{timestamp_str}.{nonce}"
    expected_signature = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected_signature):
        return False

    try:
        age = time.time() - int(timestamp_str)
    except ValueError:
        return False

    return 0 <= age <= max_age_seconds


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "allow_signup": "true",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(
    client_id: str, client_secret: str, code: str, redirect_uri: str
) -> str:
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise OAuthError(data.get("error_description", data["error"]))

    return data["access_token"]


def get_authenticated_user(token: str) -> dict:
    resp = requests.get(
        USER_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()
