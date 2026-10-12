# Brevity

A daily automated morning brief published as a GitHub Pages website.

Live site: [https://deanosmith.github.io/Brevity-Web/](https://deanosmith.github.io/Brevity-Web/)

The generator pulls weather, markets, X topics, sky data, and news feeds, summarises stories with xAI, then writes:

- `index.html` for the public website
- `brevity.css` / `brevity-web.css` for styling
- `brevity.js` for the Refresh All button

PDF generation is kept only for optional Slack delivery and is not part of the public site.

## Refresh All

The **Refresh All** button in the top bar reruns the full daily workflow, the same one the 05:00 schedule runs, with force on. It uses the same xAI tokens as a normal morning run.

1. A confirmation dialog must be accepted first.
2. The first time, it asks for a fine-grained GitHub token with **Actions: Read and write** on this repository (the same kind cron-job.org uses). The token is stored only in that browser's local storage. **Forget Token** in the dialog removes it.
3. After starting the run, the page checks every 20 seconds and reloads itself once the new brief is published (usually 2–3 minutes).

Opening or reloading the page never triggers a run or uses AI tokens.

## Sections

- Date and year progress
- Copenhagen weather (Open-Meteo), including next 2 days and peak rain time
- General stock watchlist
- Daily Proverbs or Ecclesiastes verse (no repeats until the full bank has been shown)
- For You On X: topics with a summary, why it matters, sentiment, volume, key accounts, and an overall pulse
- Sky Watch (moon, aurora, solar weather, next launch, next eclipse)
- Copenhagen news
- South Africa news
- Space news

## Automation

A daily request at 05:00 Copenhagen starts this workflow (cron-job.org). GitHub's own 05:17 / 06:17 schedule is only a backup; it is often hours late or skipped.

The job skips if today's brief is already on `main`. To regenerate, use **Run Workflow** and enable **Regenerate Even If Today Is Already Published**.

Create a fine-grained GitHub token for this repo with **Actions: Read and write**, then add one daily job at [cron-job.org](https://cron-job.org) for 05:00 `Europe/Copenhagen`:

- URL: `https://api.github.com/repos/deanosmith/Brevity-Web/actions/workflows/main.yml/dispatches`
- Method: `POST`
- Headers: `Accept: application/vnd.github+json`, `Authorization: Bearer <token>`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`
- Body: `{"ref":"main"}`

Published artifacts are committed back to `main`, which GitHub Pages serves from the repository root.

The daily verse is chosen from Proverbs and Ecclesiastes. Text comes from Crossway's [ESV API](https://api.esv.org/) when `ESV_API_KEY` is set, both locally and as the GitHub Actions secret of the same name. Without the key, or if the API fails, the bundled King James text in `resources/scripture-verses.json` is used. Shown references are stored in `resources/scripture-history.json` so the same verse is not reused until every verse has appeared. That history file is committed with each publish.

Slack delivery is disabled by default. To re-enable it temporarily, set `SEND_TO_SLACK=true` in the workflow environment and provide Slack secrets.

## Local run

```bash
uv sync
uv run python brevity.py
```

### Testing Locally With Real Data

Every run saves its full data to `resources/brief-data.json`, which is committed with each publish. To test template or styling changes against the latest real brief, with no API calls or tokens:

```bash
git pull
uv run python brevity.py --render-only
python3 -m http.server 8000
```

A full local run needs a valid `XAI_API_KEY` in `.env` (or `~/.env`); otherwise X is empty and news isn't summarised.

Useful environment variables:

- `XAI_API_KEY` required for summarisation and personalized X topics
- `ESV_API_KEY` optional; enables English Standard Version scripture (free key at [api.esv.org](https://api.esv.org/)), otherwise King James is used
- `XAI_MODEL` optional model override (default: `grok-4.20-non-reasoning`)
- `CONSUMER_KEY`, `CONSUMER_SECRET`, `ACCESS_TOKEN`, `ACCESS_TOKEN_SECRET` optional; used if the X account has Premium personalized trends
- `SEND_TO_SLACK=true` only if you explicitly want Slack upload again (generates a PDF for Slack only)
- `GENERATE_PDF=false` to force-skip PDF even when Slack is enabled

Open `index.html` locally, or serve the folder:

```bash
python3 -m http.server 8000
```

Then visit `http://localhost:8000`.
