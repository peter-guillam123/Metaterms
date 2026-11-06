# Metaterms

Kind: Work
Audience: an internal public policy colleague who tracks platform terms
Voice: plain and neutral, like a briefing note; the About page diary is in Chris's voice
Design: its own look

## What this is

A test of whether we can track changes to Meta's terms and policies over
time, using monthly copies from the Wayback Machine going back two years.
If it works, it may grow into a tool that checks the pages regularly.

## Rules for this repo

- The repo is public. Never name the organisation this is for in code, copy,
  commit messages or data.
- Don't commit whether we have agreed to any set of terms. The source
  spreadsheet has an "agreed" column; it stays out of the repo and off the
  page.
- This cloud environment can't reach web.archive.org, so fetching runs as
  a GitHub Action (`.github/workflows/fetch.yml`) that commits its results
  back to the branch. Pull before pushing.

## Things learned the hard way

- The Wayback Machine refuses new connections opened in quick succession.
  Keep one connection open and space requests 8 seconds apart; that runs at
  about 7 pages a minute with no refusals.
- Raw (`id_`) copies often come back gzip- or zstd-compressed.
- Facebook often serves the archive's crawler a login page, a "browser not
  supported" page or another language. Try the biggest copy in each month
  first, and up to three copies.
- Instagram and Facebook help-centre pages carry their text as a JSON tree
  (`json_cms_content`) inside the page's code.
- Copies with Meta's cookie banner came from European crawls and hold the
  European version of the page. Compare only within one region.
- Summaries: read every claim against the redline before publishing.
