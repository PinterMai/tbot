# Project: Personal AI Signal Bot from X (Twitter) — Telegram MVP

## Context & goals

I want a **personal Telegram bot** that:

1. Pulls posts from a curated list of X (Twitter) accounts I follow (initially ~20–50 handles I'll provide).
2. Filters posts to topics I care about: **AI infrastructure bottlenecks, compute trends (CPU/GPU/TPU/photonics/neuromorphic), AI model releases, semiconductor supply chain, and related public-company / startup signals**.
3. For each interesting cluster of posts, performs **independent web research** to verify and add context (don't just trust the tweet).
4. Sends me a **digest** with recommendations:
   - **Tech trend signals** — "this is heating up, here's why, here's what to read"
   - **Investment ideas** — public tickers + private companies, with a **conviction score 0–100** and explicit reasoning
5. Has **strong anti-hallucination guardrails** (described below). I'd rather it say "not enough evidence" than invent a number.

This is **personal use only at first** — I read X already, this just amplifies signal. Later I want to evolve it toward an **autonomous trading agent**, but step 1 is read-only recommendations I manually act on.

I'm a single user. No multi-tenant. No SaaS. Run it on my machine or a small VPS.

## Stack constraints

- **Language:** Python 3.11+
- **LLM:** Anthropic Claude (I have Opus access via subscription/API — make this configurable). Use `claude-sonnet-4` for routine summarization to save tokens, `claude-opus-4` for the final ranking and recommendation step.
  - **NOTE (2026-04-27, session 1):** Latest model IDs at build time are `claude-sonnet-4-6` and `claude-opus-4-7`. Pinned in `signal_bot/llm/client.py` as `MODEL_SONNET` / `MODEL_OPUS`.
- **Bot platform:** Telegram (BotFather token, polling — no webhook/ngrok needed).
- **Storage:** SQLite (single file). No Postgres, no Redis for MVP.
- **Deploy target:** runs as a single `python bot.py` process. Optional `systemd` unit later.
  - **NOTE (Windows host):** README will document NSSM as the Windows service equivalent.

## X data ingestion — design as pluggable strategy

**Build a `TweetSource` interface** with three concrete implementations, selectable via env var `TWEET_SOURCE`:

### Strategy A (default for MVP): `twikit`
- Use the `twikit` Python library (no API key, uses cookie auth on a dedicated dummy X account).
- I will provide the `auth_token` cookie via `.env`. Document clearly in README that this account **must not be my main** (ban risk).
- Rate-limit: max 1 request / 3 sec, max ~500 tweets/day total. Use `asyncio.Semaphore`.
- Persist `last_seen_tweet_id` per handle in SQLite to do incremental fetches.
- **(2026-04-27 update)** Every twikit error → `errors` table. `/status` shows last 5. 3 consecutive empty polls → Telegram alert. 10 consecutive errors → 1h circuit breaker.

### Strategy B (fallback / safer): `nitter_rss`
- Try a list of public Nitter instances from a config file. Health-check each on startup. Drop dead ones.
- If all dead → log warning and refuse to start in this mode.
- **(2026-04-27 update)** 30-second total health-check budget on startup. If all dead, log warning and **silently fall back to twikit** (do not refuse to start).

### Strategy C (most robust, optional): `chrome_extension`
- Stub for now — just an interface + a TODO. Plan: use Claude Chrome extension or Playwright with my real logged-in profile to read timeline. Don't implement in MVP, but leave the seam.
- **(2026-04-27 update)** Out of MVP scope entirely. Reconsider only after twikit has run live for 2+ weeks.

The rest of the pipeline must be **source-agnostic** — it just consumes a stream of `Tweet` dataclass objects.

## Pipeline architecture

```
[TweetSource] → [Dedup + Filter] → [Cluster] → [Research] → [Rank] → [Telegram Digest]
     ↑                                                                        ↓
     └────────────── SQLite (state, history, sent flags) ←───────────────────┘
```

### 1. Ingest
- Pull new tweets every `POLL_INTERVAL_MIN` (default: 60 min).
- For each handle, fetch tweets newer than `last_seen_tweet_id`.
- Store raw tweet + metadata in SQLite table `tweets` with columns: `id, handle, text, created_at, url, raw_json, processed_at`.

### 2. Dedup + topic filter
- Skip retweets unless they have an added comment.
- First-pass keyword filter (config file `topics.yaml`) — cheap regex/keyword match for AI/compute/chip terms. Tweets that pass go to LLM stage. Tweets that don't are marked `filtered_out` and stored anyway (for later analysis).

### 3. Cluster
- Group related tweets from the past 24h by topic using **embeddings**.
  - **(2026-04-27 decision)** Local `sentence-transformers/all-MiniLM-L6-v2` only. Wrapped behind `signal_bot/llm/embeddings.py::embed(texts)` so the swap is one file. No Voyage in MVP.
- A cluster = ≥2 tweets from ≥2 different handles on the same theme, OR a single high-signal tweet from a handle in a `high_priority_handles` list (config).

### 4. Research (this is where anti-hallucination lives)
For each cluster, run a research step using Claude with **web search tool enabled**:

**System prompt for the research step must include:**
```
You are a research analyst. You MUST follow these rules:

1. Every factual claim in your output must be backed by either:
   (a) a tweet from the input cluster (cite by tweet URL), or
   (b) a web source you fetched in this session (cite by URL).

2. If you cannot verify a claim, write "UNVERIFIED" next to it. Do not guess.

3. For any number (price, market cap, revenue, benchmark score, date),
   you MUST cite the source URL where you read it. If you didn't read it
   in this session, write "NUMBER NOT VERIFIED — needs lookup".

4. Distinguish clearly between:
   - what the tweets claim
   - what your web sources confirm
   - your own inference

5. If the cluster is mostly speculation or hype with no primary sources,
   say so explicitly and recommend SKIP.

6. Output strict JSON matching the schema provided. No prose outside JSON.
```

**Output schema:**
```json
{
  "topic": "string",
  "summary": "string (2-3 sentences, paraphrased, no quotes >15 words)",
  "key_claims": [
    {"claim": "string", "source": "tweet_url | web_url", "verified": true}
  ],
  "tech_trend_signal": {
    "strength": 0,
    "reasoning": "string",
    "what_to_watch": ["string"]
  },
  "investment_angle": {
    "applicable": true,
    "tickers": [{"symbol": "string", "exchange": "string", "thesis": "string"}],
    "private_companies": ["string"],
    "conviction_score": 0,
    "conviction_reasoning": "string",
    "risks": ["string"],
    "time_horizon": "days|weeks|months|years"
  },
  "recommendation": "STRONG_BUY | BUY | WATCH | SKIP | AVOID",
  "confidence": 0,
  "unverified_flags": ["string"]
}
```

### 5. Rank & filter for digest
- Drop anything with `confidence < 60` OR `unverified_flags.length > 2`.
- Sort by `tech_trend_signal.strength + investment_angle.conviction_score`.
- Keep top N (default 5) per digest.

### 6. Telegram digest
Format per cluster:
```
🔥 [TOPIC]
Trend: 78/100 · Conviction: 65/100 · Confidence: 81/100
Recommendation: WATCH

Summary: ...

📊 Investment angle:
- NVDA (NASDAQ) — thesis: ...
- LightMatter (private) — ...

⚠️ Risks: ...
🔍 Unverified: ...

📎 Sources: [tweet1] [tweet2] [article]
```

Use Telegram MarkdownV2 carefully (escape!). Send each cluster as a separate message so I can react to them individually. Add inline keyboard buttons: `👍 Useful`  `👎 Noise`  `📌 Track this`. Store my feedback in `feedback` table — used later for improving filtering.

## Anti-hallucination — concrete mechanisms (not just a vibe)

1. **Two-LLM verification on numbers.** After the research step, run a second small Claude call: "Here is the report. List every numeric claim and the URL it was sourced from. If any number lacks a URL, flag it." If anything flags, mark the report `confidence -= 20` and add to `unverified_flags`.

2. **Source diversity check.** Before issuing an investment recommendation with conviction > 70, require ≥2 independent web sources beyond the original tweets. If not met, cap conviction at 60 and append `"single-source"` to risks.

3. **Recency check.** For any cited URL, fetch and check the publication date. If > 90 days old and the topic is "current state", flag as `stale-source`.

4. **No fabricated tickers.** Maintain a local validated tickers cache (CSV from a free source, refreshed weekly). Any ticker the LLM emits that isn't in the cache → strip it and flag `"ticker_not_validated:XYZ"`.

5. **Explicit "I don't know" budget.** The system prompt above forces UNVERIFIED labels. The digest formatter must surface them — don't hide them.

6. **No financial advice framing.** Every digest message ends with: "Personal research notes, not financial advice. Verify before acting."

## Telegram bot commands

- `/start` — pair (only your Telegram user ID is allowed, set in `.env` as `ALLOWED_USER_ID`. Reject all others silently.)
- `/digest` — force-run the pipeline now
- `/handles` — list tracked handles
- `/add @handle` — add handle to tracking
- `/remove @handle` — remove
- `/topics` — list topic keywords
- `/status` — last poll time, tweets fetched, clusters made, errors
- `/pause` and `/resume` — stop scheduled polling

## Project layout

```
signal_bot/
├── .env.example
├── README.md
├── pyproject.toml          # uv / poetry
├── config/
│   ├── handles.yaml        # initial handle list
│   ├── topics.yaml         # keywords + priority weights
│   └── nitter_instances.yaml
├── signal_bot/
│   ├── __init__.py
│   ├── main.py             # entry point, scheduler
│   ├── bot.py              # telegram handlers
│   ├── sources/
│   │   ├── base.py         # TweetSource protocol
│   │   ├── twikit_source.py
│   │   ├── nitter_source.py
│   │   └── chrome_source.py  # stub
│   ├── pipeline/
│   │   ├── filter.py
│   │   ├── cluster.py
│   │   ├── research.py     # Claude calls + web search
│   │   ├── verify.py       # anti-hallucination second pass
│   │   └── rank.py
│   ├── storage/
│   │   ├── db.py           # SQLite schema + helpers
│   │   └── tickers.py      # validated ticker cache
│   ├── llm/
│   │   ├── client.py       # Anthropic SDK wrapper
│   │   └── prompts.py      # all prompts as constants
│   └── format/
│       └── telegram_md.py  # safe MarkdownV2 escaping
└── tests/
    ├── test_filter.py
    ├── test_verify.py
    └── fixtures/
        └── sample_tweets.json
```

## What I want you to do, in order

1. **Read this entire spec back to me in 5 bullets** to confirm understanding. Flag anything unrealistic.
2. Ask me for: my Telegram bot token, my Telegram user ID, the initial list of X handles, my Anthropic API key handling preference (env var vs config), and the dummy X auth_token (only after I confirm I have a dummy account).
3. Create the repo skeleton (all files above, even if some are TODO stubs).
4. Implement in this order, with me reviewing after each:
   - Step A: SQLite schema + Telegram bot skeleton with `/start`, `/status`, allowlist enforcement.
   - Step B: `twikit` source + ingest loop, write tweets to DB. No LLM yet.
   - Step C: Filter + cluster.
   - Step D: Research step with Claude + web search + the strict JSON schema.
   - Step E: Verify pass + ticker validation + ranking.
   - Step F: Telegram digest formatter + inline buttons + feedback storage.
   - Step G: README with setup instructions including the dummy-account warning.
5. After each step, **show me how to test it manually before moving on**.

## What I do NOT want

- No Docker for MVP. Plain Python.
- No fancy web UI. Telegram only.
- No auto-trading, no broker integration, no order execution. Recommendations only.
- No "trust me" claims from the LLM — every number needs a URL or an UNVERIFIED tag.
- No reproducing tweet text verbatim in digests beyond ~15 words. Paraphrase + link.

## Working with Claude Code — session continuity

You are running as Claude Code with file system access on my machine. The project will be built across many sessions because of context limits. Follow these rules to maintain continuity:

### 1. Maintain a `PROGRESS.md` file at repo root

Update it at the **start and end of every session**.

**At the start of every new session**, your first action is: read `PROGRESS.md`, read the original spec (saved as `SPEC.md`), then ask me one question: "I see we're at Step X. Continue from there, or change direction?"

**At the end of every session**, your last action is: update `PROGRESS.md` with what you finished, what's next, and any open questions. Then commit it.

### 2. Maintain a `CLAUDE.md` file at repo root

This is your **persistent operating instructions** — Claude Code reads it automatically.

### 3. Commit discipline

After every working change, commit with a descriptive message. Before context gets tight, also commit work-in-progress on a `wip/` branch.

### 4. When you hit context limit signs

If you notice you're losing track of earlier decisions, or your responses are getting slower/shorter, **proactively tell me**: "Context is filling up. Let me update PROGRESS.md and commit, then start a fresh session."

### 5. First session protocol

1. Create the repo skeleton.
2. Save this entire spec as `SPEC.md` at repo root.
3. Create `PROGRESS.md` with all steps unchecked.
4. Create `CLAUDE.md` with the rules.
5. `git init` and initial commit.
6. Then start Step A and stop when it works.

---

## Session-1 user clarifications (2026-04-27)

These resolve the reality-check flags raised at session start. They override anything contradictory above.

1. **Embeddings:** local `sentence-transformers/all-MiniLM-L6-v2` only, behind `signal_bot/llm/embeddings.py::embed(texts)`. No Voyage in MVP.
2. **Model IDs:** `claude-opus-4-7` and `claude-sonnet-4-6`, pinned as `MODEL_OPUS` / `MODEL_SONNET` constants in `llm/client.py`. All call sites import these — no hardcoded model strings elsewhere.
3. **Nitter:** build the seam, but health-check budget capped at 30 seconds on startup. If all dead → log warning + silently fall back to twikit. **Chrome/Playwright is OUT of MVP scope** — reconsider only after twikit runs live for 2+ weeks.
4. **twikit fragility (Step B requirements):**
   - Every twikit error → `errors` table (timestamp, handle, error_type, message, traceback).
   - `/status` shows last 5 errors.
   - 3 consecutive empty polling cycles across all handles → Telegram alert "ingest may be broken".
   - Circuit breaker: 10 consecutive errors → pause polling for 1h.
5. **Windows host:**
   - Use `pathlib.Path` everywhere — never string-concat paths.
   - LF line endings enforced via `.gitattributes`.
   - README "Running as a service on Windows" uses NSSM (Task Scheduler is mentioned only as alternative, no `.service` files).
6. **Secrets:** all secrets via `.env` (gitignored). `.env.example` has empty values; user fills in. Claude must NEVER ask the user to paste tokens in chat.
