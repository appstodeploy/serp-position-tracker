# 🔍 SERP Position Tracker

Streamlit app that tracks Google Search ranking positions for Persian-language
cryptocurrency queries in the Iranian market, using the [Serper.dev](https://serper.dev)
API, and exports branded XLSX reports.

Built to the spec in [`PRD.md`](./PRD.md).

## Features
- **Global Config** — brand identity, logo, Serper API key, region/language/device/page-depth.
- **Template Manager** — query templates with `{column}` placeholders (CRUD, persisted).
- **Tracking Dashboard** — upload coin list → generate queries → preview → run with live progress.
- **Reports** — run history with re-downloadable, self-contained XLSX reports.
- Brand-position detection, per-query error isolation, rate-limit back-off, RTL-aware UI.
- **Batched & crash-safe tracking** — large runs (thousands of queries) are split into batches; progress is saved after every batch, so a crash, refresh or geo-block never loses finished work or re-spends API credits — just click **Resume**.
- **Durable Google Sheets storage** — optional: stream results to a Google Sheet you own so runs survive even a full Streamlit Cloud reboot (which wipes the app's temporary disk). Essential for large-scale runs on the free tier.
- **Private per-user API keys** — each user enters their own key; it lives only in their Streamlit session and is never written to disk or shared.
- **Proxy support** — route Serper calls through an HTTP/SOCKS proxy when `google.serper.dev` is geo-blocked on your network (e.g. from Iran).

## API key & privacy model
Your Serper key is held **only in your browser session** (`st.session_state`) — it is never saved to `config.json` or committed to the repo, so no other user can see it. Enter it on **⚙️ Global Config → Your Serper API key** and click *Save key*.

For single-owner convenience you may instead set the key once via `st.secrets` or the `SERPER_API_KEY` env var (in `.env`); it will only prefill *your* session. For a public multi-user deployment, leave it unset so every visitor supplies their own.

## "API key is invalid" — it's usually a network block, not your key
`google.serper.dev` is fronted by Google infrastructure that geo-blocks some IPs (notably Iran), returning an HTML `403` that is **not** an auth error. The app now detects this and tells you to use a proxy. Fix it by setting **Proxy** on the Global Config page, e.g.:

```
socks5://127.0.0.1:1080
http://user:pass@host:port
```

Then click *Test API key* again.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional: put SERPER_API_KEY here, or enter it in the UI
streamlit run app.py
```

## Usage
1. **⚙️ Global Config** — enter brand name + Serper API key, set search params, save. Use *Test API key* to verify.
2. **📋 Template Manager** — add templates such as `خرید {fa_name}`, `قیمت {en_name}`, `{symbol} price in Iran`.
3. **🚀 Tracking Dashboard** — upload your coin list (XLSX), pick templates, generate, review config, **Run Tracking**, then download the XLSX. Big runs are processed in batches (size set on Global Config); if a run is interrupted, return to this page and click **Resume tracking** — already-fetched queries are not charged again, and you can download partial results at any time.
4. **📊 Reports** — revisit and re-download any past run.

### Coin list format
First sheet, with at least these columns (extra columns become extra placeholders):

| fa_name | en_name | symbol |
|---|---|---|
| بیت‌کوین | Bitcoin | BTC |

A ready-made example is in [`sample_coins.xlsx`](./sample_coins.xlsx).

## Privacy & access control
Streamlit Community Cloud apps are reachable by URL, and **every page (including
Reports) has its own URL** — so the whole app is gated, not just the home page.

**Enable the password gate:**
1. Pick a strong password.
2. On Streamlit Cloud: **App → Settings → Secrets**, add:
   ```toml
   app_password = "your-strong-password"
   ```
   For local runs: copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml`
   (git-ignored), or set `APP_PASSWORD=... streamlit run app.py`.
3. Redeploy. Visitors now hit a lock screen on **every** page; a “Log out” button
   appears in the sidebar. If no password is set, the app shows a public-mode warning.

**Other things that help privacy:**
- **Secrets stay out of the repo** — `app_password` and any `SERPER_API_KEY` live in
  Streamlit secrets / env, never in code. The Serper key is per-session (see above).
- **Reports are app-only** — generated XLSX files live in the app's ephemeral
  filesystem (git-ignored) and are reachable only through the gated UI, not as
  public links.
- **Don't share the app URL** publicly; treat it as a secret alongside the password.
- For stronger control, use Streamlit's **private app** + viewer allowlist (paid),
  put it behind **Cloudflare Access / a reverse proxy with auth**, or swap the
  single password for per-user logins via `streamlit-authenticator`.
- Rotate the password (and Serper key) periodically; anyone who had the old one
  loses access on the next change.

## Large-scale runs (thousands of queries) on Streamlit Cloud
Streamlit Community Cloud gives each app an **ephemeral disk that is erased on
every platform reboot** — and a long single run (e.g. 6000+ queries) is exactly
what triggers a reboot. The local batch checkpoint survives a browser refresh
but **not** a platform reboot, so for large runs enable **durable Google Sheets
storage**:

1. In Google Cloud, create a **service account**, enable the **Google Sheets
   API** and **Drive API**, and download its **JSON key**.
2. Create a blank **Google Sheet** and **share it as Editor** with the service
   account's `client_email`.
3. Add to Streamlit secrets (see `.streamlit/secrets.toml.example`):
   ```toml
   gsheet_id = "the-id-from-your-sheet-url"

   [gcp_service_account]
   type = "service_account"
   private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
   client_email = "name@project.iam.gserviceaccount.com"
   # ...rest of the JSON key fields...
   ```
4. Reboot the app. On **⚙️ Global Config** click **Test Google Sheets
   connection** to verify; the **🚀 Tracking Dashboard** then shows
   *“🟢 Durable storage ON”*.

With this on, each batch's rows are appended to your sheet and dropped from
memory (so memory stays flat and the app won't be rebooted for resource use).
If the app *is* restarted, just re-upload the same coin list, select the same
templates, **Generate**, and click **Resume tracking** — the app reads how far
it got from the sheet and continues without re-charging finished queries. Also
recommended: smaller **Batch size** (250–400) and the **one-batch-per-click**
mode for maximum safety.

## Report structure
- **Summary** — run metadata + brand header/logo.
- **Results (full)** — one row per SERP result (position, page, domain, title, URL, snippet, is-brand).
- **Brand Positions** — filtered rows where the brand domain matched.

## Project layout
```
app.py                 Streamlit entry point
pages/                 Config · Templates · Dashboard · Reports
core/                  serper · query_engine · xlsx_writer · config_store
data/                  config.json · templates.json · runs_history.json (runtime)
outputs/               generated XLSX reports
assets/                brand logo
```
