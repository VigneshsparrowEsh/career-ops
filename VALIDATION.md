# Delivery validation

Verified on October 3, 2026, on the user's Windows computer using Python 3.12.14.

## Live collection

The first collection fetched 17 Remotive listings and 2,998 Arbeitnow records. Five incomplete Arbeitnow records were rejected. After normalization and conservative duplicate handling, the database contains **2,994 unique jobs**, **1,404 companies** and **379 remote listings**. Source IDs, original links, raw source payloads and normalized job details are stored.

The first run is reported as **partial** because of the five rejected records. Valid records were saved. Closure detection was disabled for Arbeitnow during that run. The rejected-record quarantine was added after that initial collection; future rejected payloads are retained, while the first run retains its count and warning in the run log.

Live coverage was verified for Remotive and Arbeitnow. Greenhouse and Lever were validated using representative adapter fixtures; the example employer boards remain disabled.

## Automated checks

**24 tests passed** using `python -m unittest discover -s tests -v`. Coverage includes:

- All four adapters, pagination, full descriptions and schema validation.
- HTML conversion, skill boundaries, unknown values and inference metadata.
- Idempotent imports, updates, source-link preservation and version history.
- Inactive/reappearing listings, incomplete snapshots, rejected records and source isolation.
- Daily schedule slots in India time, polling intervals, Remotive request budgets and collector locking.
- Parameterized search, CSV export, formula-safe cells and SQLite backup.
- HTTP search, pagination, detail responses, security headers, collection-token checks, origin checks, host checks and invalid parameters.

JavaScript syntax was checked. Browser verification confirmed the live dashboard, a Python skill filter returning **415 jobs**, readable job details and original-source links. Collection history and CSV behavior are covered by the HTTP/data tests and manual browser checks.

## Windows daily task

Task **JobHarvest-Daily** is installed and enabled with the project's Python runtime and working directory. A launch check completed with **LastTaskResult = 0**. The test launch skipped source requests because their cooldowns had not elapsed, confirming the task could run without unnecessary repeated scraping.

Windows reported **NextRunTime = October 4, 2026, 00:00 India time** and trigger boundary `2026-10-04T00:00:00+05:30`. Midnight itself is a future event at delivery; its execution is not claimed as already observed.

The task uses the current user's interactive logon, runs without elevated privileges, allows battery operation and catches up after missed starts. It requires the user to be signed in and internet access for collection.

## Company career expansion — October 3, 2026

Added dashboard and CLI company career discovery, Ashby's public job feed, and Google's public HTML/sitemap connector. The updated local dashboard is at **http://127.0.0.1:8766**; the launcher and CLI defaults now use that port. Restarting the existing port-8765 process was rejected by automatic approval review, so the updated service was started separately.

**36 automated tests passed.** New coverage verifies board detection (including embeds and EU Lever), bounded career-page discovery, offsite/private-network rejection, robots wildcard/query rules, independent site failures, configuration preservation and deduplication, Ashby fields and exclusion of unlisted jobs, Google format validation, rotating cursors, no closure during partial batches, and authenticated asynchronous company-site requests.

Live imports were completed for **Google, Stripe, Airbnb, Cloudflare, Razorpay, Figma, Notion, Ramp, Linear, Palantir and Spotify**. The resulting database contains **5,224 active unique jobs, 1,418 companies and 597 remote listings**. The pre-expansion snapshot is preserved in **data/jobs-before-company-sites.sqlite3**.

Google imported **50 jobs** with full descriptions, locations, skills and original career links. Its public sitemap exposed **3,486 job links**. The connector imports the first search page plus up to 30 additional sitemap entries per run, persists its position, and continues in later runs. Google search pagination is blocked by robots rules and was not requested. Sitemap contents and search counts can differ, and job links can be stale. Google's snapshot remains partial; it does not claim all Google roles have been imported, and closure detection is disabled. Undocumented embedded timestamps remain unknown rather than being labeled publication dates.

Stripe, Cloudflare and Ramp supplied a small number of incomplete records; those records were rejected and quarantined while valid jobs were retained. Complete feeds and partial snapshots retain the existing source isolation, polling and duplicate rules.

Browser checks confirmed the company-sites form and saved board list, Google filtering returning **50 jobs**, readable full descriptions and original links. Submitting Google's URL again returned **1 already enabled** and added no duplicate board. The browser showed no JavaScript warnings or errors. The existing daily task reads the new saved sources without reinstallation.

## Meta and reusable scraper expansion — October 3, 2026

**46 automated tests passed.** Additional checks cover Meta URL and sitemap validation, full descriptions, separated qualifications and compensation, rotating batch cursors, retained progress after rate limits, invalid detail pages, reusable JobPosting graphs and arrays, distinct listing links, source identity, bounded public-page retries, and preservation of the latest actual collection status after cooldown skips.

Meta's public sitemap exposed **1,070 job links**. The first live batch imported **30 full job listings**, bringing the database to **5,254 active unique jobs**. Subsequent collections continue from the saved cursor in batches of up to 30. This is partial inventory; automatic closure detection remains disabled. The pre-Meta database is preserved in **data/jobs-before-meta.sqlite3** and the current consistent backup is **data/jobs-backup.sqlite3**.

Browser checks confirmed Meta's saved source, collection coverage warning, a Meta filter returning 30 jobs, and readable details with locations, responsibilities, minimum/preferred qualifications, original links and published compensation. The browser reported no JavaScript warnings or errors. The existing midnight task automatically includes Meta through the saved configuration. The reusable structured-page parser was verified with representative fixtures; it does not claim support for every company system or automatic internet-wide discovery.

## Delivery artifacts

Source project, configuration, local launchers, task installation/removal scripts, populated local SQLite database, consistent backups, build plan, instructions and this validation record. Local databases and personal files are excluded from Git.

## Personalized Career-Ops app — November 3, 2026

Connected the existing JobHarvest checkout to `https://github.com/VigneshsparrowEsh/career-ops.git` on branch `feat/jobharvest-personalized-search`. The repository's existing Vignesh résumé/profile initialized the ignored canonical `cv.md` and `config/profile.yml`. The user's requested targets are business analysis, supply chain, data analysis, market research, operations and project management. India is the home country, Remote has a separate tab, and relocation/work authorization remain unconfirmed unless edited. Source files and the UI are versioned; the database, notes, résumé, runtime path and personal preferences remain local.

**58 Python tests pass**, including authenticated profile writes, match ordering and filtering before pagination, remote country/region restrictions, unknown eligibility, irrelevant/specialist titles, skill boundaries, experience gaps, staleness, home-based versus hybrid classification, saved/hidden jobs, notes, profile preservation and validation, and idempotent handoff to Career-Ops' existing inbox. JavaScript syntax and Git whitespace checks pass. Career-Ops' `test-all.mjs --quick` reports 66 checks passed, and the profile-sync/pipeline validators pass. The full upstream suite's optional Go terminal-dashboard build could not run because Go is not installed; its Unix-shell leak/path checks also emit Windows path errors, so those checks are not claimed as an independent privacy audit. The new web app is independent of that Go dashboard.

Live public-board imports added **314 Canonical jobs and 24 Oyster jobs**, both successful. Canonical's stored workplace labels were re-normalized from the saved source payloads to recognize Home Based roles; no additional network fetch was made and observation timestamps were preserved. Remote.com and GitLab discovery returned unsupported and were not counted as connected sources. The existing Windows daily task remains Ready and reads the expanded configuration.

The resulting local collection contains **21,911 jobs, 21,187 marked active, 4,401 companies and 2,307 remote listings**. The final personalized views return **380 potential matches and 179 remote potential matches** with default settings. These are preliminary matches, including cases requiring eligibility review; they are not verified applications or a claim that every job fits. Original source links and observation dates are retained. Location mismatches are excluded from personal views by default. Missing salary, visa eligibility and unknown fields are not invented.

Browser checks verified the main/remote/saved views, profile prefill and save, temporary shortlist creation/removal, original job links, full descriptions and visible fit reasons/gaps/eligibility warnings. The browser reported no JavaScript warnings or errors. Fixture HTTP tests verify the queue endpoint creates the existing Career-Ops inbox/JD handoff without inventing an evaluation, tracker row or application. Fit is rules-based, separate from Career-Ops' A–G evaluation. Full evaluations and application submission are not automated by this app.

Title filtering, bulk relationship reads, covering SQLite indexes and a bounded change-aware match cache improved a measured first local match computation to about 3 seconds and a subsequent remote computation to 0.2 seconds; timing varies with caches and the computer. The pre-change database is preserved in `data/jobs-before-personalization.sqlite3`.
