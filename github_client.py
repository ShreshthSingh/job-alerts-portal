"""Wrapper around the bits of the GitHub REST API the portal needs to turn
a logged-in user's GitHub account into a running copy of the job scraper:
fork the template repo, commit their config, set their Telegram secrets,
and turn on Actions.

Every call takes the caller's own OAuth access token - nothing here is
ever run with a token we store server-side.
"""

import base64
import time

import nacl.encoding
import nacl.public
import requests

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"


class GitHubClientError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _request(method: str, token: str, path: str, **kwargs) -> requests.Response:
    resp = requests.request(
        method, f"{API_BASE}{path}", headers=_headers(token), timeout=20, **kwargs
    )
    if resp.status_code >= 400:
        raise GitHubClientError(
            f"{method} {path} failed ({resp.status_code}): {resp.text[:300]}",
            status_code=resp.status_code,
        )
    return resp


def get_repo(token: str, owner: str, repo: str) -> dict | None:
    resp = requests.request(
        "GET", f"{API_BASE}/repos/{owner}/{repo}", headers=_headers(token), timeout=20
    )
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise GitHubClientError(f"GET repo failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def fork_repo(token: str, template_owner: str, template_repo: str) -> None:
    """Forking your own already-forked repo is a no-op on GitHub's side, so
    this is safe to call every time someone clicks Deploy."""
    _request("POST", token, f"/repos/{template_owner}/{template_repo}/forks")


def wait_for_fork(
    token: str, user: str, repo: str, attempts: int = 15, delay_seconds: float = 2.0
) -> dict:
    """Forks are created asynchronously - poll until the repo is reachable."""
    last_error = None
    for _ in range(attempts):
        try:
            info = get_repo(token, user, repo)
            if info is not None:
                return info
        except GitHubClientError as exc:
            last_error = exc
        time.sleep(delay_seconds)

    raise GitHubClientError(
        f"Fork of {repo} didn't become ready in time"
        + (f" (last error: {last_error})" if last_error else "")
    )


def get_file_sha(token: str, owner: str, repo: str, path: str, ref: str) -> str | None:
    resp = requests.get(
        f"{API_BASE}/repos/{owner}/{repo}/contents/{path}",
        headers=_headers(token),
        params={"ref": ref},
        timeout=20,
    )
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise GitHubClientError(
            f"GET contents failed ({resp.status_code}): {resp.text[:300]}",
            status_code=resp.status_code,
        )
    return resp.json()["sha"]


def get_file_content(token: str, owner: str, repo: str, path: str, ref: str) -> str | None:
    resp = requests.get(
        f"{API_BASE}/repos/{owner}/{repo}/contents/{path}",
        headers=_headers(token),
        params={"ref": ref},
        timeout=20,
    )
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise GitHubClientError(
            f"GET contents failed ({resp.status_code}): {resp.text[:300]}",
            status_code=resp.status_code,
        )
    return base64.b64decode(resp.json()["content"]).decode("utf-8")


def put_file(
    token: str,
    owner: str,
    repo: str,
    path: str,
    content_text: str,
    message: str,
    branch: str,
    sha: str | None = None,
) -> None:
    body = {
        "message": message,
        "content": base64.b64encode(content_text.encode("utf-8")).decode("utf-8"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    _request("PUT", token, f"/repos/{owner}/{repo}/contents/{path}", json=body)


def get_public_key(token: str, owner: str, repo: str) -> tuple[str, str]:
    resp = _request("GET", token, f"/repos/{owner}/{repo}/actions/secrets/public-key")
    data = resp.json()
    return data["key_id"], data["key"]


def encrypt_secret(public_key_b64: str, secret_value: str) -> str:
    """Seal a secret the way GitHub's Actions secrets API requires."""
    public_key = nacl.public.PublicKey(
        public_key_b64.encode("utf-8"), nacl.encoding.Base64Encoder()
    )
    sealed_box = nacl.public.SealedBox(public_key)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def put_secret(token: str, owner: str, repo: str, secret_name: str, secret_value: str) -> None:
    key_id, public_key = get_public_key(token, owner, repo)
    encrypted_value = encrypt_secret(public_key, secret_value)

    _request(
        "PUT",
        token,
        f"/repos/{owner}/{repo}/actions/secrets/{secret_name}",
        json={"encrypted_value": encrypted_value, "key_id": key_id},
    )


def enable_actions(token: str, owner: str, repo: str) -> None:
    _request(
        "PUT",
        token,
        f"/repos/{owner}/{repo}/actions/permissions",
        json={"enabled": True, "allowed_actions": "all"},
    )


def get_workflow_id(token: str, owner: str, repo: str, workflow_path: str) -> int:
    resp = _request("GET", token, f"/repos/{owner}/{repo}/actions/workflows")
    for workflow in resp.json().get("workflows", []):
        if workflow["path"] == workflow_path:
            return workflow["id"]

    raise GitHubClientError(f"Workflow {workflow_path} not found in {owner}/{repo}")


def enable_workflow(token: str, owner: str, repo: str, workflow_id: int) -> None:
    _request(
        "PUT", token, f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/enable"
    )


def dispatch_workflow(token: str, owner: str, repo: str, workflow_id: int, ref: str) -> None:
    _request(
        "POST",
        token,
        f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches",
        json={"ref": ref},
    )
