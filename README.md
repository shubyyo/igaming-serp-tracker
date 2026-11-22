# iGaming SERP Volatility Index

A daily index of how much Google's mobile top 10 moves for gambling keywords, per market. Fetches that look like poisoned SERPs are re-checked before they count. The output is a static site (`site/`), a CSV, and embeddable SVG badges. Everything runs on GitHub Actions and Cloudflare Pages. The code uses only the Python standard library, so there's nothing to install.

```
config/      markets, draft keyword baskets, settings, chart annotations
tracker/     collector, poison guard, scoring, site/badge builder, simulator, CLI
data/days/   one gzipped JSON per day: status and canonical top-10 for every keyword
data/state/  pending task IDs between steps; monthly frozen reference percentiles
site/        the public page; site/data and site/badge are generated
tests/       unittest suite
site/igaming-serp-volatility/methodology/  the methodology page (source of truth)
```

Public URL: `https://tools.seotipsy.com/igaming-serp-volatility/` (the site root is reserved for a tools index). The page lives in `site/igaming-serp-volatility/`.

## Data providers

`settings.json → provider`, or the GitHub repository variable `TRACKER_PROVIDER`, picks the provider (`serpapi` or `dataforseo`). Using the variable means you can switch without a commit.

| | SerpApi (current) | DataForSEO (target) |
|---|---|---|
| How | Synchronous; `collect` fetches, `resolve` re-fetches | Standard queue; `post` queues, `collect` pulls |
| Fresh results | `no_cache=true` on every call | always fresh |
| Poison repair | none (our guard only) | `resolve_poisoned_results` + our guard |
| Need for 8 × 50 | ~420 searches/day, ~12,600/month: Production plan (15k) or bigger | ~$15/month |

Run `python -m tracker quota` to see the SerpApi plan, the searches left and how many days they cover. If a run would exceed the searches left, `collect` stops and tells you to switch provider.

**Switching.** Every stored result records its provider. On the first day after a switch, each keyword is re-baselined instead of scored, because two providers see slightly different SERPs. The chart gets an automatic "Data provider" annotation for that day.

## Daily cycle (UTC)

| Time  | Workflow     | What happens |
|-------|--------------|--------------|
| 06:00 | `1-post`     | Queue one Standard-queue task per keyword (mobile, depth 10, `resolve_poisoned_results: true`). |
| 07:30 | `2-collect`  | Pull the results, canonicalise URLs, run the guard, queue one re-fetch per suspect keyword, publish. |
| 09:00 | `3-resolve`  | Pull the re-fetches, settle yesterday's quarantines, close the day, publish. |

GitHub's cron can start runs late, sometimes by 15 minutes or more. `collect` waits up to `collect_wait_minutes` for tasks, and anything still unfinished is recorded as `missing`.

## Commands

```
python -m tracker simulate --days 150   # synthetic run into ./sim (no API calls); preview sim/site
python -m tracker cost                  # expected monthly spend for the configured basket
python -m tracker post | collect | resolve
python -m tracker rebuild               # regenerate site/data and badges from data/days
python -m unittest discover -s tests -t .
```

To preview locally, run `python -m http.server -d sim/site 8000`.

## Setup (your accounts, your clicks)

1. **SerpApi (temporary).** In GitHub, go to repo, then Settings, then Secrets and variables, then Actions. Add the secret `SERPAPI_KEY` and the variable `TRACKER_PROVIDER` = `serpapi`. Locally, put `SERPAPI_KEY=...` in a `.env` file in the repo root (gitignored). Never paste the key into chat or commit it.
   **DataForSEO (later).** Create an account and add credit. In GitHub, go to repo, then Settings, then Secrets and variables, then Actions. add the secrets `DATAFORSEO_LOGIN` and `DATAFORSEO_PASSWORD`, using the API login and password from the dashboard, not your site password. For a free dry run, add the repository variable `DATAFORSEO_BASE` = `https://sandbox.dataforseo.com` and trigger the workflows by hand.
2. **GitHub repo.** Push this folder and enable Actions. In Settings, then Actions, then General, set workflow permissions to "Read and write".
3. **Cloudflare Pages.** Create a project connected to the repo. Leave the build command empty and set the output directory to `site`. Under Custom domains, add `tools.seotipsy.com`. Cloudflare creates the CNAME itself because the zone is already on Cloudflare.
4. **Keywords.** The v1 baskets (50 per market, built from Ahrefs on 2026-11-21) are frozen in `config/keywords/<market>.txt`. Turn markets on or off in `config/markets.json`.
5. **Baseline.** Let it run 30 days before publicising. Until then each market is scored against its starting reference in `config/calibration.json` (or the `settings.json → bootstrap_reference` placeholder where there is none), and the page says so.

## Cost

Standard queue: $0.0006 per 10-result page, plus the same again per task for `resolve_poisoned_results`, so $0.0012 per keyword-day.

| Basket | Per day | Per 30 days | Per year |
|---|---|---|---|
| 8 × 50 = 400 keywords, 5% re-fetched | ~$0.50 | ~$15 | ~$185 |
| current draft lists (189 keywords)   | ~$0.24 | ~$7  | ~$87  |

GitHub Actions minutes are free on a public repo. A private repo gets 2,000 minutes a month, which is enough unless `collect` regularly waits its full 20 minutes. Cloudflare Pages Free allows 500 builds a month; this uses about 90.

## Design choices that differ from the handoff

* **Host-level identity.** Results are compared by host name, not full URL (`tracker/canonical.py → identities`). Google now hides most destination URLs behind `google.*/goto` redirects (seen in SerpApi on mobile and desktop, 2026-11-21); only the displayed host is reliable. A host's second result in the same top 10 becomes `host#2`. Set `pipeline.IDENTITY = "url"` to go back to URL level if a provider returns real links.

* **Continuity override.** The guard never flags a fetch that matches the last verified day (Jaccard ≥ 0.7). Without this, a real SERP shift is "suspect" on every following day until it enters the 3-of-7 reference set.
* **Provisional days instead of gaps.** On a real update day, both fetches disagree with history but agree with each other. Those days are quarantined and published as provisional. The next day either confirms them as a `confirmed_shift` or discards them. "Degraded" (fewer than 80% usable keywords) is judged after that resolution, so update days are kept rather than shown as missing data.
* **Consensus baseline.** `v_k` compares today with each host's mean rank over the keyword's last `consensus_days` (3) verified days, none older than `lookback_days + consensus_days − 1`, not with yesterday alone. Mobile SERPs flip between result sets from fetch to fetch. On the pre-launch sample the cross-market correlation of day-to-day changes in V rose from 0.26 (yesterday) to 0.35 (3 days); levels went from 0.42 to 0.58. Five and seven days scored about the same on changes and react more slowly. A keyword is scored once it has 3 verified days. A provider switch empties the consensus and restarts that warm-up, so the chart has a 3-day gap there.
* **Score.** The score is piecewise linear through the series' frozen percentiles: P50 → 5, P80 → 7, P95 → 9, P99 → 10, reaching 0 at `max(0, 2 × P50 − P99)`. So the score and the band always agree. Daily `V` sits in a narrow range (P99 is only about 1.3 × P50 on real data), which made the older `10 × V / max(P99, 3 × P50)` read 3–4 every day.
* **Guard.** A first fetch is suspect when ≥ 70% of its hosts were not seen for that keyword in the last 7 days, counting verified days and every fetch, flagged ones included. Or when it shows a social/forum spike. The earlier rule ("shares < 50% of hosts seen on 3 of the last 7 days") flagged about 40% of ordinary mobile fetches. Counting flagged fetches as seen stops a real change from locking a keyword out.
* **The global index** is the equal-weighted mean of the market indexes, and needs at least 4 usable markets. It has its own frozen reference.
* **References** are P50/P80/P95/P99 of the trailing 90 non-degraded, non-provisional days before each month starts, frozen in `data/state/references.json`. A month with fewer than 30 such days uses the series' starting reference from `config/calibration.json` if it has one, else `bootstrap_reference` (set to the all-markets calibration; DE and ES have no calibration of their own). Those stand-ins are re-derived on every rebuild (not frozen), so a corrected calibration applies immediately.
* **Calibration.** `config/calibration.json` holds only derived percentiles. They were measured on a pre-launch sample of daily mobile SERPs (2 Oct – 22 Nov 2026) for a separate casino/betting keyword set per market, run through the same identities, guard and scoring as the live index. Flagged fetches were dropped, since the sample has no re-fetch. The raw sample isn't in this repo.
* **Warm-up.** For a keyword's first 3 verified days there's no history to compare against, so nothing is flagged. The 30-day baseline covers this.

## Changing CSS or JS

Cloudflare caches `.css` and `.js` at its edge. When you change `site/igaming-serp-volatility/assets/*`, bump the `?v=` number on every `<link>`/`<script>` that loads them (index, methodology, tools index, 404), or visitors keep the old file.

## Caveats to keep in public copy

* Always describe detections as "suspected" poisoning. The guard infers it from how a SERP compares with its own history.
* Don't present Google's behaviour as confirmed policy.
* The September 2026 spam update window (24 Sep – 8 Oct, as reported) is annotated for context only. It predates the baseline.
