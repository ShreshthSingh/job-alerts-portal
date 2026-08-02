"""Self-serve portal: log in with GitHub, pick companies/searches, plug in
your own Telegram bot, and get your own fork of the scraper running on
your own GitHub Actions schedule.

No database, no server-side storage: your GitHub token only ever lives in
this browser session's st.session_state and is gone when the tab closes.
"""

import json

import requests
import streamlit as st

import github_client
import oauth

st.set_page_config("Job Alerts - Self Serve", page_icon="🔔", layout="wide")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap');
    html, body, [class*="css"], .stApp, .stApp * {
        font-family: 'JetBrains Mono', monospace !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

TEMPLATE_OWNER = st.secrets["TEMPLATE_REPO_OWNER"]
TEMPLATE_REPO = st.secrets["TEMPLATE_REPO_NAME"]
CLIENT_ID = st.secrets["GITHUB_CLIENT_ID"]
CLIENT_SECRET = st.secrets["GITHUB_CLIENT_SECRET"]
REDIRECT_URI = st.secrets["REDIRECT_URI"]

WORKFLOW_PATH = ".github/workflows/job-scraper.yml"

DEFAULT_COMPANIES = [
    {
        "name": "Paypal",
        "api_url": "https://paypal.wd1.myworkdayjobs.com/wday/cxs/paypal/jobs/jobs",
        "base_url": "https://paypal.wd1.myworkdayjobs.com/en-US/jobs/job",
    },
    {
        "name": "athenahealth",
        "api_url": "https://athenahealth.wd1.myworkdayjobs.com/wday/cxs/athenahealth/External/jobs",
        "base_url": "https://athenahealth.wd1.myworkdayjobs.com/en-US/External/job",
    },
    {
        "name": "iHeartMedia",
        "api_url": "https://iheartmedia.wd5.myworkdayjobs.com/wday/cxs/iheartmedia/External_iHM/jobs",
        "base_url": "https://iheartmedia.wd5.myworkdayjobs.com/en-US/External_iHM/job",
    },
    {
        "name": "Intel",
        "api_url": "https://intel.wd1.myworkdayjobs.com/wday/cxs/intel/External/jobs",
        "base_url": "https://intel.wd1.myworkdayjobs.com/en-US/External/job",
    },
]


# -----------------------------
# Workday facet helpers (same logic as the local app.py config tool)
# -----------------------------

WORKDAY_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def fetch_facets(api_url: str) -> dict | None:
    try:
        resp = requests.post(api_url, json={}, headers=WORKDAY_HEADERS, timeout=30)
    except requests.RequestException as exc:
        st.error(f"Couldn't reach {api_url}: {exc}")
        return None

    if resp.status_code != 200:
        st.error(f"API error {resp.status_code} fetching facets")
        return None

    try:
        return resp.json()
    except requests.exceptions.JSONDecodeError:
        st.error(
            "That company's career site didn't return usable data (it "
            "may be blocking automated requests). Try a different company, "
            "or add it manually with the exact filter IDs if you already "
            "have them."
        )
        return None


def parse_facets(data: dict) -> dict:
    facet_map = {}
    for facet in data.get("facets", []):
        param = facet.get("facetParameter")

        if param == "locationMainGroup":
            for group in facet.get("values", []):
                for loc in group.get("values", []):
                    facet_map.setdefault("locations", {})[loc["descriptor"]] = loc["id"]
            continue

        values = facet.get("values", [])
        if not values:
            continue
        facet_map[param] = {v["descriptor"]: v["id"] for v in values}

    return facet_map


def send_test_telegram(bot_token: str, chat_id: str) -> tuple[bool, str]:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "This is a test message from your job alerts setup. If you got this, your bot is wired up correctly!",
            },
            timeout=15,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            return True, "Test message sent - check Telegram."
        return False, data.get("description", f"HTTP {resp.status_code}")
    except requests.RequestException as exc:
        return False, str(exc)


# -----------------------------
# Session state defaults
# -----------------------------

st.session_state.setdefault("gh_token", None)
st.session_state.setdefault("gh_user", None)
st.session_state.setdefault("companies", DEFAULT_COMPANIES.copy())
st.session_state.setdefault("portal_config", [])
st.session_state.setdefault("config_version", 0)


def set_portal_config(new_config: list) -> None:
    """The single write path for portal_config. Bumping config_version
    forces the raw-JSON editor (keyed on it) to reset and show this new
    value, instead of silently keeping stale text a user hasn't looked at."""
    st.session_state["portal_config"] = new_config
    st.session_state["config_version"] += 1


# -----------------------------
# GitHub login
# -----------------------------

def do_login_screen():
    st.title("Job Alerts - Self Serve")
    st.write(
        "Get your own scheduled job alert scraper running on your own GitHub "
        "account, sending alerts to your own Telegram bot - for free, with "
        "no ongoing involvement from us."
    )
    st.write(
        "Logging in grants access to fork a repo into your account, commit "
        "a config file to it, and set two Action secrets on it. We never "
        "store this access - it only lives in your browser tab for this "
        "session."
    )

    # Signed, self-verifying state (see oauth.make_signed_state) - Streamlit
    # resets session_state on the full-page reload GitHub's redirect causes,
    # so there's nothing to remember it against; it has to check itself.
    state = oauth.make_signed_state(CLIENT_SECRET)
    authorize_url = oauth.build_authorize_url(CLIENT_ID, REDIRECT_URI, state)

    # Not st.link_button: it opens in a new tab, which starts a brand new
    # Streamlit session with no memory of oauth_state, so the callback's
    # state check always fails. target="_top" (not "_self") navigates the
    # whole top-level tab even when the app itself is shown inside an
    # iframe (e.g. Streamlit Cloud's own dashboard preview) - "_self" would
    # try to load github.com inside that iframe, which GitHub blocks.
    st.markdown(
        f'<a href="{authorize_url}" target="_top" '
        'style="display:inline-block;padding:0.5em 1em;background:#ff4b4b;'
        'color:white;border-radius:0.5em;text-decoration:none;font-weight:600;">'
        "Login with GitHub</a>",
        unsafe_allow_html=True,
    )
    st.caption(
        "If clicking that does nothing (some embedded/preview views block "
        "top-level navigation), copy this URL into a fresh browser tab instead:"
    )
    st.code(authorize_url, language=None)


def handle_oauth_callback():
    params = st.query_params
    code = params.get("code")
    returned_state = params.get("state")

    if not code:
        return False

    if not oauth.verify_signed_state(CLIENT_SECRET, returned_state):
        st.error("Login link expired or invalid - please try logging in again.")
        st.query_params.clear()
        return False

    try:
        token = oauth.exchange_code_for_token(CLIENT_ID, CLIENT_SECRET, code, REDIRECT_URI)
        user = oauth.get_authenticated_user(token)
    except (oauth.OAuthError, requests.RequestException) as exc:
        st.error(f"Login failed: {exc}")
        st.query_params.clear()
        return False

    st.session_state["gh_token"] = token
    st.session_state["gh_user"] = user
    st.query_params.clear()
    return True


if not st.session_state["gh_token"]:
    if handle_oauth_callback():
        st.rerun()
    else:
        do_login_screen()
        st.stop()


# -----------------------------
# Authenticated app
# -----------------------------

token = st.session_state["gh_token"]
username = st.session_state["gh_user"]["login"]

# Load whatever's already deployed on this user's fork into the session,
# once per session, before they start editing. Without this, a fresh
# session (every revisit - nothing persists) starts with an empty alert
# list, and Deploy's config.json write would blindly overwrite the fork
# with just that empty/partial list, silently dropping everything already
# deployed.
if not st.session_state.get("loaded_existing_config"):
    st.session_state["loaded_existing_config"] = True
    try:
        existing_repo = github_client.get_repo(token, username, TEMPLATE_REPO)
        is_our_fork = (
            existing_repo is not None
            and existing_repo.get("fork")
            and existing_repo.get("parent", {}).get("full_name")
            == f"{TEMPLATE_OWNER}/{TEMPLATE_REPO}"
        )
        if is_our_fork:
            content = github_client.get_file_content(
                token, username, TEMPLATE_REPO, "config.json", existing_repo["default_branch"]
            )
            if content:
                loaded = json.loads(content)
                set_portal_config(loaded)
                st.info(
                    f"Loaded your existing alert list ({len(loaded)} companies) from "
                    "your fork - it's below. Edit it and hit Deploy to update, or "
                    "leave it as-is and use Reactivate instead."
                )
    except (github_client.GitHubClientError, requests.RequestException, ValueError):
        pass  # no existing fork/config yet, or a hiccup reading it - fine to start fresh

with st.sidebar:
    st.write(f"Logged in as **{username}**")
    if st.button("Log out"):
        st.session_state["gh_token"] = None
        st.session_state["gh_user"] = None
        st.rerun()

    st.divider()
    st.header("Add a company")
    with st.form("add_company"):
        new_name = st.text_input("Name")
        new_api = st.text_input("Workday API URL")
        new_base = st.text_input("Base URL")
        add_submit = st.form_submit_button("Add")

    if add_submit:
        if not (new_name and new_api and new_base):
            st.sidebar.error("All fields required")
        elif any(c["name"].lower() == new_name.lower() for c in st.session_state["companies"]):
            st.sidebar.warning("Already in your list")
        else:
            st.session_state["companies"].append(
                {"name": new_name.strip(), "api_url": new_api.strip(), "base_url": new_base.strip()}
            )
            st.sidebar.success("Added")

st.title("Build your alert list")

names = [c["name"] for c in st.session_state["companies"]]
selected_name = st.selectbox("Company", names)
company = next(c for c in st.session_state["companies"] if c["name"] == selected_name)

if st.button("Fetch filters"):
    with st.spinner("Loading..."):
        data = fetch_facets(company["api_url"])
    if data:
        st.session_state["facets"] = parse_facets(data)
        st.success("Loaded")

if "facets" in st.session_state:
    facets = st.session_state["facets"]
    st.divider()
    st.subheader("Filters")

    selected_facets = {}
    col1, col2 = st.columns(2)

    with col1:
        for key in ["jobFamilyGroup", "jobFamilies", "workerSubType"]:
            if key in facets:
                sel = st.multiselect(key, facets[key].keys(), key=f"f_{key}")
                if sel:
                    selected_facets[key] = [facets[key][s] for s in sel]

    with col2:
        if "locations" in facets:
            sel = st.multiselect("locations", facets["locations"].keys(), key="f_locations")
            if sel:
                selected_facets["locations"] = [facets["locations"][s] for s in sel]

    search = st.text_input("Search text", "software engineer")

    if st.button("Add to my alert list"):
        new_entry = {
            "name": company["name"],
            "api_url": company["api_url"],
            "base_url": company["base_url"],
            "params": {
                "appliedFacets": selected_facets,
                "limit": 20,
                "offset": 0,
                "searchText": search.replace(" ", "+"),
            },
        }

        existing = list(st.session_state["portal_config"])
        replaced = False
        for i, cfg in enumerate(existing):
            if cfg["name"].lower() == new_entry["name"].lower():
                existing[i] = new_entry
                replaced = True
                break
        if not replaced:
            existing.append(new_entry)
        set_portal_config(existing)

        st.success(f"{'Updated' if replaced else 'Added'} {company['name']} in your alert list")

st.divider()
st.subheader("Your alert list")
st.caption(
    "This is exactly what gets committed as config.json on Deploy - the "
    "picker above is a convenience for building it, but you can edit the "
    "raw JSON directly here too (e.g. to fix a company whose filter IDs "
    "went stale, remove one, or hand-tweak searchText). Click 'Apply "
    "edits' before Deploy for changes here to actually take effect."
)

edited_text = st.text_area(
    "Alert list (JSON)",
    value=json.dumps(st.session_state["portal_config"], indent=4),
    height=350,
    key=f"config_editor_{st.session_state['config_version']}",
)

if st.button("Apply edits"):
    try:
        parsed = json.loads(edited_text)
        if not isinstance(parsed, list):
            raise ValueError("Top-level JSON must be a list of companies")
        for entry in parsed:
            if not isinstance(entry, dict) or not entry.get("name") or not entry.get("api_url"):
                raise ValueError("Every entry needs at least a 'name' and 'api_url'")
    except (json.JSONDecodeError, ValueError) as exc:
        st.error(f"Invalid JSON, not applied: {exc}")
    else:
        set_portal_config(parsed)
        st.success(f"Applied - {len(parsed)} companies. Click Deploy below to push this to your fork.")
        st.rerun()

if not st.session_state["portal_config"]:
    st.caption("Nothing added yet - fetch filters for a company above and add it.")

st.divider()
st.subheader("Test a company's API")
st.caption(
    "Paste an API URL, base URL, and a params JSON (same shape as an "
    "entry's 'params' field above) to check whether Workday actually "
    "returns jobs for them right now - handy for confirming a fix to "
    "stale facet IDs works before adding/updating an entry above, or for "
    "sanity-checking a brand new company."
)

test_api_url = st.text_input(
    "API URL",
    key="test_api_url",
    placeholder="https://company.wd1.myworkdayjobs.com/wday/cxs/company/site/jobs",
)
test_base_url = st.text_input(
    "Base URL (optional, only used to build the preview links below)",
    key="test_base_url",
    placeholder="https://company.wd1.myworkdayjobs.com/en-US/site/job",
)
test_params_text = st.text_area(
    "Params (JSON)",
    value=json.dumps(
        {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "software engineer"},
        indent=4,
    ),
    height=180,
    key="test_params_text",
)

if st.button("Test API"):
    if not test_api_url:
        st.error("Enter an API URL first")
    else:
        try:
            test_params = json.loads(test_params_text)
        except json.JSONDecodeError as exc:
            st.error(f"Invalid params JSON: {exc}")
        else:
            try:
                resp = requests.post(
                    test_api_url, json=test_params, headers=WORKDAY_HEADERS, timeout=30
                )
            except requests.RequestException as exc:
                st.error(f"Couldn't reach {test_api_url}: {exc}")
            else:
                if resp.status_code == 400:
                    st.error(
                        "HTTP 400 - Workday rejected these params (usually means one "
                        "or more facet IDs are stale or invalid)."
                    )
                elif resp.status_code != 200:
                    st.error(f"HTTP {resp.status_code} from that API URL.")
                else:
                    try:
                        data = resp.json()
                    except requests.exceptions.JSONDecodeError:
                        st.error(
                            "Got a 200 but the response wasn't JSON - possibly a "
                            "bot-block page or a Workday outage page. Try again "
                            "shortly."
                        )
                    else:
                        postings = data.get("jobPostings")
                        if postings is None:
                            st.warning(
                                "No 'jobPostings' field in the response - here's the "
                                "raw JSON so you can see what came back instead:"
                            )
                            st.json(data)
                        elif not postings:
                            st.warning(
                                "Request succeeded (so the params themselves are "
                                "valid), but 0 jobs matched right now."
                            )
                        else:
                            st.success(f"{len(postings)} job(s) returned:")
                            for job in postings:
                                title = job.get("title", "(no title)")
                                posted = job.get("postedOn", "")
                                link = job.get("externalPath") or job.get("jobPostingUrl") or ""
                                if test_base_url and link:
                                    slug = (
                                        link.split("/job/")[-1]
                                        if "/job/" in link
                                        else link.split("/")[-1]
                                    )
                                    st.markdown(
                                        f"- **{title}** - {posted}  \n  {test_base_url}/{slug}"
                                    )
                                else:
                                    st.markdown(f"- **{title}** - {posted}")

st.divider()
st.subheader("Telegram bot")
st.caption(
    "Create a free bot via @BotFather on Telegram, then message it once so it "
    "can DM you - that gives you the chat id below (e.g. via @userinfobot, or "
    "https://api.telegram.org/bot<token>/getUpdates)."
)
st.caption(
    "Note: nothing on this page is saved between visits (see Privacy below) "
    "- if you're just checking on alerts you already deployed, skip this and "
    "use 'Reactivate my alerts' further down instead of re-entering these."
)
bot_token = st.text_input("Bot token", type="password", key="bot_token")
chat_id = st.text_input("Chat id", key="chat_id")

if st.button("Send test message"):
    if not (bot_token and chat_id):
        st.error("Enter both bot token and chat id first")
    else:
        ok, message = send_test_telegram(bot_token, chat_id)
        (st.success if ok else st.error)(message)

st.divider()
st.subheader("Deploy")
st.caption(
    "This forks the scraper into your GitHub account, commits your alert "
    "list, sets your bot token/chat id as encrypted secrets on your fork, "
    "and turns on its GitHub Actions schedule - all under your own account."
)
st.warning(
    "Heads up: GitHub auto-disables scheduled Actions on a repo after 60 "
    "days with no activity. If your alerts ever go quiet, just come back "
    "here, log in, and click Reactivate below - no need to rebuild anything."
)
st.caption(
    "**Deploy** = commits your current company list (always needed) and, "
    "only if you filled in the Telegram fields above, updates your bot/chat "
    "id too - leave them blank to keep whatever's already set from before. "
    "**Reactivate** = you're not changing anything, just restarting/"
    "confirming an already-deployed schedule (needs nothing but login)."
)

deploy_col, reactivate_col = st.columns(2)

with deploy_col:
    if st.button("Deploy my alerts", type="primary"):
        if not st.session_state["portal_config"]:
            st.error("Add at least one company to your alert list first")
        else:
            try:
                with st.spinner("Forking repo..."):
                    try:
                        github_client.fork_repo(token, TEMPLATE_OWNER, TEMPLATE_REPO)
                        repo_info = github_client.wait_for_fork(token, username, TEMPLATE_REPO)
                    except github_client.GitHubClientError as exc:
                        if exc.status_code in (403, 404):
                            st.error(
                                "You don't have access to this yet - it's invite-only. "
                                "Ask the admin to add you as a collaborator on the "
                                "source repo, then come back and try again."
                            )
                            st.stop()
                        raise
                default_branch = repo_info["default_branch"]

                with st.spinner("Committing your config..."):
                    sha = github_client.get_file_sha(
                        token, username, TEMPLATE_REPO, "config.json", default_branch
                    )
                    github_client.put_file(
                        token,
                        username,
                        TEMPLATE_REPO,
                        "config.json",
                        json.dumps(st.session_state["portal_config"], indent=4),
                        "Update job alert config via self-serve portal",
                        default_branch,
                        sha=sha,
                    )

                if bot_token and chat_id:
                    with st.spinner("Setting secrets..."):
                        github_client.put_secret(
                            token, username, TEMPLATE_REPO, "BOT_TOKEN", bot_token
                        )
                        github_client.put_secret(
                            token, username, TEMPLATE_REPO, "CHAT_ID", chat_id
                        )
                else:
                    st.info(
                        "Telegram fields left blank - leaving your existing bot "
                        "token/chat id as-is. If you've never deployed before, "
                        "fill those in first or your alerts won't be able to send."
                    )

                with st.spinner("Enabling Actions..."):
                    github_client.enable_actions(token, username, TEMPLATE_REPO)
                    workflow_id = github_client.get_workflow_id(
                        token, username, TEMPLATE_REPO, WORKFLOW_PATH
                    )
                    github_client.enable_workflow(token, username, TEMPLATE_REPO, workflow_id)
                    github_client.dispatch_workflow(
                        token, username, TEMPLATE_REPO, workflow_id, default_branch
                    )

                repo_url = f"https://github.com/{username}/{TEMPLATE_REPO}"
                st.success("Deployed! A run just started - check Telegram in a minute.")
                st.markdown(f"[Your repo]({repo_url}) · [Actions tab]({repo_url}/actions)")

            except (github_client.GitHubClientError, requests.RequestException) as exc:
                st.error(f"Deploy failed: {exc}")

with reactivate_col:
    if st.button("Reactivate my alerts"):
        try:
            with st.spinner("Reactivating..."):
                repo_info = github_client.get_repo(token, username, TEMPLATE_REPO)
                if repo_info is None:
                    st.error("No fork found yet - use Deploy first.")
                else:
                    default_branch = repo_info["default_branch"]
                    github_client.enable_actions(token, username, TEMPLATE_REPO)
                    workflow_id = github_client.get_workflow_id(
                        token, username, TEMPLATE_REPO, WORKFLOW_PATH
                    )
                    github_client.enable_workflow(token, username, TEMPLATE_REPO, workflow_id)
                    github_client.dispatch_workflow(
                        token, username, TEMPLATE_REPO, workflow_id, default_branch
                    )
                    st.success("Reactivated! A run just started - check Telegram in a minute.")
        except (github_client.GitHubClientError, requests.RequestException) as exc:
            st.error(f"Reactivate failed: {exc}")
