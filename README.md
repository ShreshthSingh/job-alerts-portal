# Job Alerts Portal

Self-serve web app: log in with GitHub, pick companies/searches from
Workday-powered career sites, plug in your own Telegram bot, and get your
own scheduled scraper running on your own GitHub account.

This repo is just the portal (a stateless Streamlit app - no database, no
stored tokens). The actual scraper that gets forked into each user's
account lives in a separate, private repo. Splitting it this way means
this repo can stay public (so Streamlit Community Cloud can deploy it with
zero permission setup) while the scraper template - and its config,
history, etc. - stays private, gated by GitHub's own collaborator system.

## How it works

1. Visitor logs in with GitHub (OAuth, scope `repo`).
2. They build a list of companies/filters and a Telegram bot token/chat id.
3. On "Deploy," the portal forks the private template repo into their
   account, commits their config, encrypts and sets their Telegram
   credentials as GitHub Actions secrets, and turns on the fork's
   schedule.
4. Because the template repo is private, only accounts the admin has added
   as a **collaborator** on it can actually fork it - that's the entire
   access-control mechanism. Anyone can open this portal and log in, but
   Deploy fails with a clear message for anyone not yet invited.

Nothing here is stored server-side: a visitor's GitHub token lives only in
their browser session for that visit.

## Files

- `streamlit_app.py` - the app itself (GitHub login, facet/config builder,
  Telegram test-send, Deploy/Reactivate buttons)
- `oauth.py` - manual GitHub OAuth2 code flow (GitHub isn't OIDC, so
  Streamlit's built-in `st.login()` can't be used)
- `github_client.py` - fork / commit-file / encrypt-and-set-secret /
  enable-Actions API calls, each using the visitor's own token
- `.streamlit/secrets.toml.example` - documents the required secrets

## Deploying this (for the admin)

### 1. GitHub OAuth App (one-time)

GitHub Settings -> Developer settings -> OAuth Apps -> New OAuth App.
Homepage URL and Authorization callback URL should both be this app's
deployed URL (you'll know it once you've picked a name in step 2 below).
Note the Client ID, and generate a Client secret.

### 2. Deploy on Streamlit Community Cloud

- share.streamlit.io -> New app -> pick this repo/branch, main file path
  `streamlit_app.py`. Being public, there's no extra GitHub permission
  step needed.
- Pick a custom app URL/subdomain if you want to know it in advance
  (needed for step 1).

### 3. Secrets (Streamlit Cloud -> app -> Settings -> Secrets)

```toml
GITHUB_CLIENT_ID = "..."
GITHUB_CLIENT_SECRET = "..."
REDIRECT_URI = "https://<your-app>.streamlit.app"
TEMPLATE_REPO_OWNER = "ShreshthSingh"
TEMPLATE_REPO_NAME = "job-scraper"
```

### 4. Invite people

The template repo (`ShreshthSingh/job-scraper`) is private. Repo ->
Settings -> Collaborators and teams -> Add people (by GitHub username or
the email on their GitHub account). Only invited accounts can fork it and
therefore only they can successfully deploy from this portal.

### 5. Share the app URL

## Known limitation

GitHub auto-disables **scheduled** Actions on a repo (including forks)
after 60 days of no repository activity. There's no server-side fix for
this without storing every user's token or running a central keepalive
service, which is deliberately avoided here to keep this free and
stateless. The portal's "Reactivate my alerts" button lets a returning
user turn their schedule back on in one click - no rebuild needed.

## Telegram bot setup (for end users, ~1 minute)

1. Message **@BotFather** on Telegram, send `/newbot`, follow the prompts
   -> you get a bot token.
2. Message your new bot anything (so it's allowed to DM you back).
3. Get your chat id via **@userinfobot**, or by visiting
   `https://api.telegram.org/bot<token>/getUpdates` after messaging your
   bot and reading the `chat.id` field.
4. Paste both into the portal and hit "Send test message."
