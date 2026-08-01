"""GitHub OAuth2 (authorization code flow) helpers.

GitHub's OAuth apps are not OIDC-compliant (no id_token / discovery
document), so Streamlit's built-in `st.login()` can't be used. This is a
small manual implementation of the same flow.
"""

from urllib.parse import urlencode

import requests

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"

SCOPE = "repo"


class OAuthError(RuntimeError):
    pass


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
