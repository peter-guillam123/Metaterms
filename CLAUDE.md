# Metaterms

Kind: Work
Audience: an internal public policy colleague who tracks platform terms
Voice: plain and neutral, like a briefing note; the About page diary is in Chris's voice
Design: its own look (not Guardian)

## What this is

A test of whether we can track changes to Meta's terms and policies over
time, using monthly copies from the Wayback Machine going back two years.
If it works, it may grow into a tool that checks the pages regularly.

## Rules for this repo

- The repo is public. Never mention the Guardian by name in code, copy,
  commit messages or data.
- Don't commit whether we have agreed to any set of terms. The source
  spreadsheet has an "agreed" column; it stays out of the repo and off the
  page.
- This cloud environment can't reach web.archive.org, so fetching runs as
  a GitHub Action (`.github/workflows/fetch.yml`) that commits its results
  back to the branch. Pull before pushing.
