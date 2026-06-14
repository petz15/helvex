# Crawler & HTML Ingestion — Design / Improvement Plan

Scope: improve the HTTP crawler, the Playwright crawler, site-overview discovery, bot-protection
handling, and design the missing **HTML → structured data** ingestion stage.

## Current state (as built)

```
web_url_populate  → company_url_candidates (best = selected)
web_crawl_http    → httpx + bs4 → homepage + keyword-matched subpages → HTML to S3
   ├ js_required  → escalates to tier=playwright
   ├ bot_blocked  → TERMINAL (not escalated)
web_crawl_playwright → Chromium + playwright-stealth (js_required / explicit playwright tier)
company_web_pages.needs_extraction = TRUE   ← set on every page, NEVER consumed
[Future] web_extract → does not exist
```

Files: `crawler_http.py`, `crawler_playwright.py`, `crawler_common.py`,
`job_handlers/web_crawl.py`, `crud/crawler.py`. S3 key: `crawl/{company_id}/{page_type}.html`.

**Three gaps, in priority order:**
1. **No extraction stage** — raw HTML sits in S3 unused. The whole point (contacts, description,
   service keywords feeding scoring/NOGA) is unbuilt. This is the highest-value work.
2. **Naive site overview** — subpages are discovered only from homepage nav/footer anchor-text
   keyword matching (`find_subpage_links`). No `robots.txt`, no `sitemap.xml`. Misses impressum/
   contact pages that aren't linked in the main nav, and ignores crawl-delay.
3. **Bot protection is detect-only** — `detect_bot_block` flags blocks but there is no mitigation:
   no TLS/HTTP-2 fingerprint matching, no client-hints headers, no retry/backoff, and a bot_block
   at HTTP tier is terminal instead of escalating to Playwright.

---

## A. Site overview / sitemap discovery

Add a lightweight site-map step before page selection. New module `crawler_sitemap.py` (shared by
both tiers; pure-stdlib + httpx, no new heavy deps).

- **robots.txt**: fetch `/robots.txt` once per domain → extract `Sitemap:` directives and
  `Crawl-delay`. Feed crawl-delay into the existing per-domain `rate_limit()`.
- **sitemap.xml**: try declared sitemaps, then `/sitemap.xml`, `/sitemap_index.xml`. Recurse one
  level into sitemap-index files. Cap at e.g. 500 URLs/domain.
- **URL classification**: merge sitemap URLs + homepage nav links, then classify each by **URL path
  AND anchor text** against the existing `_SUBPAGE_PRIORITY` keyword sets (reuse them — extend
  `find_subpage_links` to also accept a URL list). This reliably finds `/impressum`, `/kontakt`,
  `/datenschutz` even when not in the visible nav.
- Optional: persist the discovered inventory (page count, languages via hreflang, has-sitemap flag)
  as a per-company signal — useful for scoring "site completeness".

Net effect: better page targeting, fewer wasted fetches, polite crawl-delay compliance, and a real
"site overview" rather than guessing from nav anchors.

---

## B. Bot-protection improvements

### HTTP tier (`crawler_http.py`) — biggest cheap wins

- **TLS / HTTP-2 fingerprint** *(highest impact)*: httpx sends an HTTP/1.1 + non-Chrome TLS
  (JA3/JA4) fingerprint that Cloudflare/Akamai flag immediately regardless of headers. Switch the
  homepage fetch to **`curl_cffi`** with `impersonate="chrome"` (drop-in `requests`-like async API,
  matches Chrome's TLS+H2 fingerprint). Keep httpx as fallback. This alone defeats a large share of
  "soft" blocks without a browser.
- **Client-hints headers**: the request currently omits `sec-ch-ua*` entirely — a contradiction
  with a Chrome User-Agent and a known bot tell. Add `Sec-Ch-Ua`, `Sec-Ch-Ua-Mobile`,
  `Sec-Ch-Ua-Platform` **consistent with the chosen UA** (couple them in the UA pool so UA + hints
  always match).
- **Modernize + couple the UA pool**: Chrome 125 is dated; bump to current and make each pool entry
  a `(user_agent, sec_ch_ua, platform)` tuple so they never disagree.
- **Retry + backoff instead of terminal fail**: on `429`/`503`/bot_blocked, honor `Retry-After`,
  set `next_crawl_at` (already in the model + claim query) using exponential backoff keyed on
  `consecutive_failures` (already tracked). Stops hammering and recovers transient blocks.
- **Escalate bot_blocked → Playwright**: currently terminal. Route `cloudflare`/`js_challenge`
  types to `tier=playwright` (like `js_required`) so the browser tier gets a shot before giving up.

### Playwright tier (`crawler_playwright.py`)

- **Use real Chrome channel**: `launch(channel="chrome")` instead of bundled Chromium — far better
  fingerprint and passes more Cloudflare JS challenges (requires Chrome in the ml image).
- **Resource blocking**: route-abort images/fonts/media (`page.route`) — 2–4× faster, lower memory,
  smaller footprint. We only need DOM + text, not pixels.
- **Light human signals**: small randomized viewport jitter, a short scroll, and `wait_until=
  "domcontentloaded"` + a bounded settle instead of `networkidle` (networkidle hangs on sites with
  long-polling/analytics — a current timeout source).
- **3rd fallback tier (optional)**: for hard Cloudflare/CAPTCHA, reuse the existing external-scrape
  vendor pattern (`scrapingdog_search_client.py` already integrates ScrapingDog) as a paid
  `tier=external` last resort, gated by org/credits. Keeps cost bounded to the few sites that need it.

---

## C. HTML → structured data ingestion (`web_extract` job) — the core new build

New job handler `handle_web_extract` + module `crawler_extract.py`. Claims
`company_web_pages WHERE needs_extraction = TRUE` (the partial index already exists), batched and
chunked (700k-row discipline), reads HTML from S3, writes structured fields, flips
`needs_extraction=FALSE` + `extracted_at`.

**Extraction layers (cheap-deterministic first, LLM optional/gated):**

1. **Main-content / boilerplate strip** — `trafilatura` (lang-aware, strips nav/footer/cookie
   junk) → clean main text + page metadata. Store clean text (drives keywords + optional LLM).
2. **Structured signals (deterministic, no API cost):**
   - **Contacts**: emails (regex + `mailto:`), phones via `phonenumbers` (CH formats), social
     links (linkedin / xing / facebook / instagram).
   - **Schema.org / JSON-LD / OpenGraph**: parse `Organization` / `LocalBusiness` → name, address,
     phone, email, `sameAs`, founding date, opening hours.
   - **Swiss UID/MWST** (`CHE-xxx.xxx.xxx` regex) — a strong, verifiable company-match signal; can
     auto-confirm the selected URL belongs to the company and feed `verified_business`.
   - **Languages** offered (hreflang), `has_contact_form` (already captured at crawl time).
   - **Service/product keywords** from cleaned text → feed existing keyword extraction → NOGA +
     clustering.
3. **LLM layer (optional, gated)** — Claude Haiku on the cleaned main text only (bounded tokens):
   short company description + service summary + category hint. Reuse the existing
   `claude_classify` credit/tier gating and batch-API pattern; never run ungated.

**New table `company_web_extract`** (1 row per company, latest extraction):
`company_id PK`, `emails[]`, `phones[]`, `socials jsonb`, `address`, `uid`, `languages[]`,
`description`, `service_keywords[]`, `extraction_method`, `confidence`, `extracted_at`. Cascade on
company delete; mirror the SQLite-compat `_ArrayOfText` pattern from `company_crawl_state`.

**Downstream wiring:**
- Service keywords merge into the existing keyword/NOGA/cluster pipeline.
- Verified UID/address can raise `web_score` and assist `verified_business`.
- Company profile page shows extracted contacts + description (per CLAUDE.md frontend-wiring rule —
  **confirm exact UI placement before building**).

---

## Recommended sequencing

| Phase | Work | Effort / value |
|---|---|---|
| 1 | `web_extract` job (layers 1–2 deterministic only) + `company_web_extract` table + frontend display | High value, no API cost — unlocks the dormant S3 HTML |
| 2 | `curl_cffi` impersonation + client-hints + retry/backoff + bot_blocked→playwright escalation | High value, low effort — recovers blocked sites |
| 3 | `crawler_sitemap.py` (robots + sitemap discovery, crawl-delay) | Medium — better targeting & politeness |
| 4 | Playwright: chrome channel, resource blocking, settle tuning | Medium — speed + harder sites |
| 5 | LLM extraction layer (gated) + external-scrape fallback tier | Optional — bounded cost |

## Reuse / don't reinvent
- `_SUBPAGE_PRIORITY`, `find_subpage_links`, `parse_soup`, `count_*`, `detect_bot_block`,
  `rate_limit`, `pick_user_agent` in `crawler_common.py`.
- `claim_crawl_batch` SKIP-LOCKED pattern + `_run_crawl_batch` loop for the extract job's batching.
- `s3_client.crawl_s3_key` / download for reading stored HTML.
- `next_crawl_at` + `consecutive_failures` (already in `company_crawl_state`) for backoff.
- Existing Claude credit/tier gating from `claude_classify` for the optional LLM layer.

## Open decisions (need user input before build)
1. Extraction granularity: one `company_web_extract` row per company (recommended) vs per page?
2. LLM layer in v1 or deterministic-only first? (recommend deterministic-only first.)
3. External paid scrape fallback tier — in scope now or later?
4. Company-profile UI: where do extracted contacts/description surface?
