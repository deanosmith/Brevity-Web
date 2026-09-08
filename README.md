# Brevity

A daily automated morning brief published as a GitHub Pages website.

Live site: [https://deanosmith.github.io/Brevity-Web/](https://deanosmith.github.io/Brevity-Web/)

The generator pulls weather, markets, X topics, sky data, and news feeds, summarises stories with xAI, then writes:

- `index.html` for the public website
- `brevity.css` / `brevity-web.css` for styling

PDF generation is kept only for optional Slack delivery and is not part of the public site.

## Sections

- Date and year progress
- Copenhagen weather (Open-Meteo), including next 2 days and peak rain time
- General stock watchlist
- Daily Proverbs or Ecclesiastes verse (no repeats until the full bank has been shown)
- Personalized topics from X
- Sky Watch (moon, aurora, solar weather, next launch, next eclipse)
- Copenhagen news
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

The daily verse is chosen from Proverbs and Ecclesiastes and loaded only from Crossway's [ESV API](https://api.esv.org/). Set `ESV_API_KEY` locally and as the GitHub Actions secret of the same name. Shown references are stored in `resources/scripture-history.json` so the same verse is not reused until every verse has appeared. That history file is committed with each publish. If the API key is missing, the Scripture panel is omitted rather than showing another translation.

Slack delivery is disabled by default. To re-enable it temporarily, set `SEND_TO_SLACK=true` in the workflow environment and provide Slack secrets.

## Local run

```bash
uv sync
uv run python brevity.py
```

Useful environment variables:

- `XAI_API_KEY` required for summarisation and personalized X topics
- `ESV_API_KEY` required for English Standard Version scripture; create a free key at [api.esv.org](https://api.esv.org/)
- `XAI_MODEL` optional model override (default: `grok-4.20-non-reasoning`)
- `CONSUMER_KEY`, `CONSUMER_SECRET`, `ACCESS_TOKEN`, `ACCESS_TOKEN_SECRET` optional; used if the X account has Premium personalized trends
- `SEND_TO_SLACK=true` only if you explicitly want Slack upload again (generates a PDF for Slack only)
- `GENERATE_PDF=false` to force-skip PDF even when Slack is enabled

Open `index.html` locally, or serve the folder:

```bash
python3 -m http.server 8000
```

Then visit `http://localhost:8000`.
