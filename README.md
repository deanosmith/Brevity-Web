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
- Christian reflection question
- Personalized topics from X
- Sky Watch (moon, aurora, solar weather, next launch, next eclipse)
- Copenhagen news
- Space news

## Automation

GitHub Actions fires several times each morning (from `03:17` to `05:47` UTC, off the hour) so delayed or dropped slots still land a published page by 07:00 Copenhagen time. Extra runs skip generation if today's brief is already on `main`. Manual dispatch always regenerates.

Published artifacts are committed back to `main`, which GitHub Pages serves from the repository root.

Slack delivery is disabled by default. To re-enable it temporarily, set `SEND_TO_SLACK=true` in the workflow environment and provide Slack secrets.

## Local run

```bash
uv sync
uv run python brevity.py
```

Useful environment variables:

- `XAI_API_KEY` required for summarisation, the reflection question, and personalized X topics
- `XAI_MODEL` optional model override (default: `grok-4.20-non-reasoning`)
- `CONSUMER_KEY`, `CONSUMER_SECRET`, `ACCESS_TOKEN`, `ACCESS_TOKEN_SECRET` optional; used if the X account has Premium personalized trends
- `SEND_TO_SLACK=true` only if you explicitly want Slack upload again (generates a PDF for Slack only)
- `GENERATE_PDF=false` to force-skip PDF even when Slack is enabled

Open `index.html` locally, or serve the folder:

```bash
python3 -m http.server 8000
```

Then visit `http://localhost:8000`.
