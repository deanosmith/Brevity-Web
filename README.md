# Brevity

A daily automated morning brief published as a GitHub Pages website.

Live site: [https://deanosmith.github.io/Brevity-Web/](https://deanosmith.github.io/Brevity-Web/)

The generator pulls weather, markets, X trends, and news feeds, summarises stories with xAI, then writes:

- `index.html` for the public website
- `resources/brevity.json` for the structured brief payload
- `brevity.pdf` as an optional downloadable archive

## Sections

- Date and year progress
- Copenhagen weather
- Stock markets
- Words of Jesus
- Personalised trends on X
- Copenhagen news
- Space news
- World news
- Stoic / proverb quote

## Automation

GitHub Actions runs daily at `04:00 UTC` (around 06:00 Copenhagen time in winter / 07:00 in summer) and on manual dispatch.

Published artifacts are committed back to `main`, which GitHub Pages serves from the repository root.

Slack delivery is disabled by default. To re-enable it temporarily, set `SEND_TO_SLACK=true` in the workflow environment and provide Slack secrets.

## Local run

```bash
uv sync
uv run python brevity.py
```

Useful environment variables:

- `XAI_API_KEY` required for summarisation and generated quotes
- `XAI_MODEL` optional model override (default: `grok-4.20-non-reasoning`)
- `CONSUMER_KEY`, `CONSUMER_SECRET`, `ACCESS_TOKEN`, `ACCESS_TOKEN_SECRET` for X trends
- `GENERATE_PDF=false` to skip PDF generation
- `SEND_TO_SLACK=true` only if you explicitly want Slack upload again

Open `index.html` locally, or serve the folder:

```bash
python3 -m http.server 8000
```

Then visit `http://localhost:8000`.
