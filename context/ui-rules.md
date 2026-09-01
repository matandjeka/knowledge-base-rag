# Advanced Multi-Source Enterprise RAG — UI Rules

## 1. Product UX Rules
1. The user must always know which knowledge sources are active.
2. Every generated answer must expose citations.
3. Retrieval debugging must be available but hidden by default.
4. Ingestion status must be explicit: queued, parsing, indexing, ready, failed.
5. The UI must never imply an answer is verified when source evidence is weak.

## 2. Main Application Areas

### Sidebar
Contains:
- Workspace selector
- New source
- Source list
- Index status
- Retrieval settings
- Admin link

### Main Chat Area
Contains:
- Conversation title
- User message
- Assistant answer
- Inline citation markers
- Confidence / evidence status
- Suggested follow-up questions

### Source Inspector
Opens when a citation is selected.
Contains:
- Source title
- Source type
- Exact passage or row
- Page/URL/table locator
- Retrieval method
- Re-ranking score
- Metadata

## 3. Source Ingestion Rules

### PDF Upload
Show:
- File name
- Size
- Parse state
- Page count when known

### Website
Require:
- Starting URL

Optional:
- Max pages
- Same-domain-only toggle
- Include/exclude URL patterns

### CSV
Show:
- Row count
- Column names
- Preview first 10 rows
- User-selectable semantic text columns
- User-selectable metadata columns

### Database
Show:
- Connection name, never password
- Approved schemas/tables
- Read-only status
- Test connection result

## 4. Chat Rules
- Disable question submission while no source is ready unless the application supports global sources.
- Stream the answer when supported.
- Render citations as interactive badges.
- Keep citations near the claims they support.
- If evidence is insufficient, show: “I could not find enough evidence in the indexed sources to answer reliably.”

## 5. Retrieval Controls
Advanced panel may expose:
- Top K per retriever
- Retriever toggles
- Fusion method
- Re-ranker toggle
- Final context count
- Metadata filter

Defaults should be safe and hidden from ordinary users.

## 6. Confidence Rules
Do not show a fake confidence percentage based solely on LLM self-report.

If confidence is shown, derive it from measurable signals such as:
- retrieval score quality
- agreement across retrievers
- re-ranker margin
- citation coverage
- answer faithfulness evaluation

## 7. Error Handling
Errors must explain:
- What failed
- Which source failed
- Whether retry is possible
- Whether previously indexed data remains available

Do not expose stack traces in production UI.

## 8. Accessibility
- All inputs require labels
- Keyboard navigation for source cards and citations
- Visible focus states
- Sufficient text/background contrast
- Do not rely on color alone for status

## 9. Empty States

### No Sources
Display a three-card source chooser:
- Upload PDF
- Add Website
- Upload CSV / Connect Database

### No Conversation
Display:
“Ask a question across your enterprise knowledge.”

### No Results
Display:
“No relevant evidence was found. Try a more specific question or add another knowledge source.”

## 10. Admin Rules
Admin screens should expose:
- Source lifecycle
- Indexed chunk counts
- Failed source jobs
- Last indexed timestamp
- Vector/graph status
- Evaluation results
- Query traces

## 11. Streamlit-Specific Rules
- Use `st.sidebar` for source management
- Use `st.chat_input` and `st.chat_message`
- Use `st.status` for ingestion progress
- Use `st.expander` for retrieval details
- Use `st.tabs` for Sources / Chat / Evaluation when useful
- Cache models and long-lived clients using Streamlit resource caching

## 12. Future Next.js UI Rules
If moved to Next.js:
- Keep backend retrieval logic in Python services
- Use server actions/API routes only for lightweight orchestration
- Do not embed vector indexing or large model execution in Vercel request handlers
