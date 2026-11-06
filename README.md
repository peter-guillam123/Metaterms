# Metaterms

How have Meta's terms and policies changed over the past two years?

This is a test. It takes one copy a month of 34 Meta terms and policy pages
from the Internet Archive's Wayback Machine, November 2024 to November 2026,
compares the wording month by month, and shows every change as a redline
with a plain-English summary.

## How it fits together

- `terms.csv` lists the pages.
- `fetch/` runs as a GitHub Action (`.github/workflows/fetch.yml`). It asks
  the Wayback Machine which copies it holds, downloads one per month and
  turns each into plain text. It commits the results to `data/`:
  - `data/index/` what the archive holds for each page
  - `data/captures/` which copy was used for each month
  - `data/text/` each distinct version of each page, as text
  - `data/last-run.log` every request the last run made
- `analyse/rules.json` holds page-specific clean-up (sidebars, menus).
  `analyse/build.py` also sets aside copies from the archive's European
  crawlers (they carry Meta's cookie banner) so like is compared with like.
- `analyse/compare.py` finds the changes between versions, sentence by
  sentence, and sizes each by the words that changed.
- `analyse/report.py` prints changes as plain-text redlines.
- `data/summaries.json` has a headline and summary for each change, written
  by Claude from the redlines. `data/highlights.json` is the short "What
  stands out" list.
- `analyse/build.py` builds the page in `site/` (`site/index.html` is the
  template with the data inlined).

## Running it

The Action runs when anything in `fetch/` or `terms.csv` changes on a
working branch. `fetch/job.txt` says what to do: `probe`, `full` or
`reextract`. Each run stops after 25 minutes and the next carries on.

To rebuild the page after a fetch:

```bash
pip install -r fetch/requirements.txt
python analyse/build.py
```

## Limits

The archive doesn't copy every page every month, its crawlers mostly see
the US version of each page, and a change made and reversed between two
copies won't show. The About page on the site has the full list.
