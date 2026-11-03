# JobHarvest build plan

## Scope agreed

Collect **all available roles and locations** from the enabled public feeds. Run daily at **00:00 Asia/Kolkata**. Store jobs locally and make them searchable. No paid services or API keys are required for the initial feeds.

## Implementation

1. **Collection — implemented.** Public-feed adapters for Remotive, Arbeitnow, Greenhouse, Lever and Ashby, plus Google and Meta Careers public HTML/sitemap adapters and a reusable JobPosting metadata parser. Add company career URLs through the dashboard or CLI. Support pagination, bounded retries, request timeouts, polling intervals, live progress, per-site health and per-source failure isolation. Google and Meta import rotating partial batches with persistent cursors.
2. **Normalization — implemented.** Preserve job titles, company names, locations, descriptions, source links, salary, employment type and publication dates where supplied. Extract skills and experience clues with conservative rules; label inferred values and keep unavailable fields unknown.
3. **Storage — implemented.** SQLite database with jobs, source listings, skills, field history, collection runs, source runs and rejected records. Update existing records on repeat collection. Merge matching source URLs or exact title/company/location/description fingerprints. Retain inactive jobs.
4. **Browsing — implemented.** Local dashboard with keyword, location, skill, company, domain, experience, source and remote filters, detail views, pagination, collection history and CSV export. JSON endpoints and command-line operations are available.
5. **Automation — implemented.** Windows Task Scheduler at midnight India time, plus an optional Python scheduler for other environments. Catch up after missed starts, prevent simultaneous collections and attempt each daily schedule slot once.
6. **Validation — implemented.** Automated tests for adapters, inference, duplicate handling, updates, closures, failure isolation, request limits, locking, timezone scheduling, database backup, CSV export and HTTP endpoints. A real first collection is stored in the included database.

## Operational boundaries

- Source coverage is limited to enabled feeds and explicitly configured company boards. This does not search the entire internet. Remotive covers remote jobs; Arbeitnow largely covers European jobs.
- Greenhouse and Lever require employer board identifiers, not credentials. Their sample entries are disabled until configured.
- Company career discovery finds supported boards and JobPosting metadata on supplied public pages; it does not search every company automatically. Google and Meta cursor search pagination are not used; the collector uses public sitemaps and job pages, with bounded batches and no closure detection. Generic structured sources cover only their supplied page. Other platforms still require their own connector.
- Inferred skills are mentions in job text, not a guarantee that every skill is mandatory. Experience clues and domains can remain unknown, especially in languages not covered by the rules.
- Closure detection runs only after complete, valid, unfiltered source snapshots. A listing is marked inactive after three missing snapshots. Failures, truncation and rejected records suspend closure detection for that source.
- Daily automation on this PC requires the user to be signed in. The PC needs internet access to collect. Missed starts are attempted when Windows can run the task.
- Local SQLite and the loopback dashboard are suitable for a personal collector. A shared hosted service would need authenticated access, a service account, deployment infrastructure and a database such as PostgreSQL.

## Possible later additions

Add desired employer boards, other documented feeds, broader skill vocabularies, multilingual experience extraction, email alerts if requested, PostgreSQL deployment, or an allowed HTML adapter for a named site. Verify that source's access rules before implementing an HTML adapter.
