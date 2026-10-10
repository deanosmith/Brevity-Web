# Brevity

A daily automated morning brief published as a GitHub Pages website.

Live site: [https://deanosmith.github.io/Brevity-Web/](https://deanosmith.github.io/Brevity-Web/)

The generator pulls weather, markets, X topics, sky data, and news feeds, summarises stories with xAI, then writes `index.html`, `brevity.css`, `brevity-web.css`, and `brevity.js`. PDF generation is only for optional Slack delivery.

## Refresh All

The **Refresh All** button reruns the full daily workflow (same as the 05:00 schedule) with force on. Opening or reloading the page never starts a run.

1. Accept the confirmation dialog.
2. The first time, paste a fine-grained GitHub token with **Actions: Read and write** on this repository. It is stored only in that browser. **Forget Token** removes it.
3. The page checks every 20 seconds and reloads once the new brief is published (usually 2–3 minutes).

## Sections

- Date and year progress
- Copenhagen weather (Open-Meteo), next 2 days, and peak rain time
- General stock watchlist
- Daily Proverbs or Ecclesiastes verse (no repeats until the full bank has been shown)
- For You On X: personalized trend names, category, volume, and time
- Sky Watch (moon, aurora, solar weather, next launch, next eclipse)
- Copenhagen, South Africa, and space news

## Automation

A daily request at 05:00 Copenhagen starts this workflow (cron-job.org). GitHub's 05:17 / 06:17 schedule is only a backup.

The job skips if today's brief is already on `main`. To regenerate, use **Run Workflow** and enable **Regenerate Even If Today Is Already Published**.

Create a fine-grained GitHub token for this repo with **Actions: Read and write**, then add one daily job at [cron-job.org](https://cron-job.org) for 05:00 `Europe/Copenhagen`:

- URL: `https://api.github.com/repos/deanosmith/Brevity-Web/actions/workflows/main.yml/dispatches`
- Method: `POST`
- Headers: `Accept: application/vnd.github+json`, `Authorization: Bearer <token>`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`
- Body: `{"ref":"main"}`

Published artifacts are committed back to `main`. Shown verse references are stored in `resources/scripture-history.json`.

Slack delivery is off by default. Set `SEND_TO_SLACK=true` and provide Slack secrets to re-enable it.

## Local run

```bash
uv sync
uv run python brevity.py
```

To restyle against the latest published data, with no API calls:

```bash
git pull
uv run python brevity.py --render-only
python3 -m http.server 8000
```

A full local run needs `XAI_API_KEY` in `.env` (or `~/.env`); otherwise X is empty and news is not summarised.

- `XAI_API_KEY` required for summarisation
- `ESV_API_KEY` optional; English Standard Version scripture (key at [api.esv.org](https://api.esv.org/)), otherwise King James
- `XAI_MODEL` optional (default: `grok-4.20-non-reasoning`)
- `CONSUMER_KEY`, `CONSUMER_SECRET`, `ACCESS_TOKEN`, `ACCESS_TOKEN_SECRET` optional X Premium personalized trends
- `SEND_TO_SLACK=true` to upload a PDF to Slack
- `GENERATE_PDF=false` to skip PDF even when Slack is enabled
