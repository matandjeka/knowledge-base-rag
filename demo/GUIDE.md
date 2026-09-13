# Meridian Robotics — demo walkthrough

A self-contained tour of the platform: one fictional company, all five source types, every
retrieval strategy, and inspectable citations. Runs entirely on your machine.

## Run it

```sh
uv sync
uv run python scripts/seed_demo.py          # add --skip-graph if you have no OpenAI key
# ... then in another terminal:
uv run streamlit run ui/streamlit_app.py    # http://localhost:8501  → Chat tab
```

`seed_demo.py` builds the demo PDFs and a read-only SQLite database, serves a small internal
website on `127.0.0.1:8900`, launches the API, and ingests six sources into the `local`
workspace:

| Source | Type | What it holds |
|---|---|---|
| HR & finance policy handbook | PDF (8 pages) | HR-402, POLICY-77, FIN-997A, retention, termination, benefits |
| Information security program | PDF (8 pages) | incident response, backups, vendors, program ownership, migration |
| Meridian service catalog | Website (4 pages) | API-GW-17, the gateway service, runbook SRE-X9 |
| Parts inventory | CSV (6 rows) | SKU → warehouse, quantity, unit cost |
| Vendor contracts | CSV (4 rows) | contract → vendor, renewal date, annual value |
| Meridian analytics | Database (SQLite, read-only) | regions, departments, products, customers, tickets, orders, sales |

The **knowledge graph** and **SQL retrieval** need an LLM. Set `OPENAI_API_KEY`,
`GRAPH_EXTRACTION_MODEL`, and `SQL_GENERATION_MODEL` before seeding to enable them; without a key
the other four source types and all vector / lexical / window / fusion / re-ranking retrieval
still work.

## What to show, by retrieval strategy

Ask these in the **Chat** tab (automatic routing) or pin a strategy in **Retrieval Lab**.
Every answer ends with `[S1]`-style markers — click a citation to see the exact supporting
passage, page, URL, or row.

### Automatic routing + fusion (the default)
- *What are the remote work guidelines?* → policy PDF, pages on POLICY-77 and Remote Work.
- *Who owns the gateway service?* → fuses the security PDF and the service-catalog website.
- *Summarize the employee handbook.* → grounded summary with page citations.

### Lexical (exact identifiers) — Retrieval Lab → strategy `lexical`
- *What does HR-402 require?* → policy PDF, "annual access reviews".
- *What is required by FIN-997A?* → "two approvals".
- *Where is SKU_88Z stored?* → inventory CSV row `SKU_88Z`, warehouse north.
- *When does CN-2048 renew?* → contracts CSV row `CN-2048`, 2028-03-31.
- *What does API-GW-17 own?* → service catalog, "authentication enforcement".

### Sentence-window (surrounding context) — strategy `sentence_window`
- *How long are records retained?* → the Records Retention paragraph in context.
- *Explain the paragraph around the termination notice.* → thirty-days-notice clause.
- *Show nearby context for backup restoration.* → "backups are tested quarterly".

### Knowledge graph (multi-hop relationships) — strategy `graph` *(needs OpenAI)*
- *Who owns Project Atlas?* → Operations.
- *Which team manages Product Beacon?* → the Platform Team.
- *Which regulation governs Product Ember?* → SEC-8.
- *Who owns the gateway service?* → Security Engineering.

### SQL (aggregation over the database) — strategy `sql` *(needs OpenAI)*
- *Which region has the highest churn?* → East.
- *Compare revenue by region.* → West 250 is the highest.
- *List open tickets older than thirty days.* → ticket 31 (billing queue).
- *Which product has the highest price?* → Titan (product 4).
- *Show the quarterly sales trend.* → sales increased in Q4.

## Things to point out

- **Citations are inspectable.** Open one to see the source-specific locator — a PDF page, a
  crawled URL, a CSV row id, or a database table/row fingerprint.
- **The Retrieval Lab** runs the same query through different strategies side by side without
  changing any backend defaults, and shows the routing trace and per-retriever contributions.
- **The Evaluation tab** renders the committed 40-case benchmark report and its deployment gates.
- **The baseline generator is extractive** — it returns the exact supporting passages with
  citation markers rather than paraphrasing, and can echo the same passage when a short source
  matches several index representations (base chunk + sentence window). Swapping in an LLM
  generator (the `Generator` protocol) produces polished prose over the same grounded evidence.
- **Keep questions single-fact.** The extractive baseline answers one claim at a time; ask
  "What does HR-402 require?" rather than "What does HR-402 require and who owns it?".
- **`demo/sources/` is the human-authored input**; `scripts/build_demo_assets.py` renders it to
  the PDFs and the database under `demo/build/` (git-ignored).
