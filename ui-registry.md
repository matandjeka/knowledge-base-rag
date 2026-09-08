# UI Registry

## Baseline — Established September 4, 2026

This baseline was established from the existing native Streamlit interface. The application uses
Streamlit theme tokens and built-in component states rather than custom CSS or hardcoded colors.

| Property | Correct pattern |
| --- | --- |
| App background | Native Streamlit theme |
| Panel background | Native Streamlit expander/status container |
| Border | Native Streamlit component border |
| Border radius | Native Streamlit component radius |
| Button primary | `st.button(..., type="primary", use_container_width=True)` |
| Button secondary | `st.button(..., use_container_width=True)` |
| Text primary | `st.title`, `st.header`, `st.subheader`, and `st.write` |
| Text secondary | `st.caption` |
| Feedback | `st.status`, `st.success`, `st.info`, and `st.error` |
| Section spacing | `st.divider` between sidebar source types |

### Database Source Controls

File: ui/streamlit_app.py
Last updated: September 4, 2026

| Property | Class or pattern |
| --- | --- |
| Background | Native `st.expander` panel |
| Border | Native Streamlit expander border |
| Border radius | Native Streamlit expander radius |
| Text — primary | Native input labels |
| Text — secondary | Input `help` text |
| Spacing | Sidebar section separated with `st.divider` |
| Hover state | Native Streamlit interactive state |
| Shadow | None |
| Accent usage | Primary connect button only |

**Pattern notes:** Keep advanced source configuration inside an expander. Use a full-width primary
button only for the final connection action, native validation feedback, and no custom CSS.

### Automatically Routed Query Chat

File: ui/streamlit_app.py
Last updated: September 4, 2026

| Property | Class or pattern |
| --- | --- |
| Background | Native `st.chat_message` and `st.expander` containers |
| Border | Native Streamlit chat and expander styling |
| Border radius | Native Streamlit component radius |
| Text — primary | `st.write` answer and question text |
| Text — secondary | `st.caption` citation, fallback, and timing text |
| Spacing | Native chat component spacing |
| Hover state | Native Streamlit multiselect, input, and expander states |
| Shadow | None |
| Accent usage | Native chat role treatment |

**Pattern notes:** Render the user question and assistant response with native chat components.
Supporting citations appear directly below the answer as muted captions. Keep route details in a
collapsed expander, with primary fields as short labeled rows and secondary diagnostics as captions.
Source filtering uses a native multiselect above the chat input. Empty and unavailable states use
native disabled input, information, and error components.

### Citation Inspector

File: ui/streamlit_app.py
Last updated: September 4, 2026

| Property | Class or pattern |
| --- | --- |
| Background | Native `st.expander` container |
| Border | Native Streamlit expander border |
| Border radius | Native Streamlit component radius |
| Text — primary | `st.write` for supported claim and bounded excerpt |
| Text — secondary | `st.caption` for retriever, score, and fingerprint |
| Spacing | Native expander content spacing |
| Hover state | Native expander and `st.link_button` states |
| Shadow | None |
| Accent usage | Website-only native link button |

**Pattern notes:** Each citation uses one collapsed expander labeled with its stable response ID,
source title or type, and human-readable locator. Show claim associations before the excerpt and
keep retrieval diagnostics visually secondary. Only website citations receive an outbound action;
PDF, CSV, and database locators remain descriptive until secured source viewing exists.

### Multipage Navigation

File: ui/streamlit_app.py
Last updated: September 4, 2026

| Property | Class or pattern |
| --- | --- |
| Background | Native Streamlit page and sidebar theme |
| Border | Native navigation group treatment |
| Border radius | Native Streamlit component radius |
| Text — primary | `st.title` per page and `st.header` in the sidebar |
| Text — secondary | `st.caption` for workspace and page purpose |
| Spacing | Native `st.navigation` page-group spacing |
| Hover state | Native `st.Page` navigation state |
| Shadow | None |
| Accent usage | Native active-page treatment and semantic page icons |

**Pattern notes:** Group end-user knowledge workflows separately from diagnostic workflows. Every
page starts with one title and a short muted purpose statement. Keep the fixed workspace visible in
the sidebar rather than repeating workspace controls on every page.

### Operational Data Panels

File: ui/pages.py
Last updated: September 4, 2026

| Property | Class or pattern |
| --- | --- |
| Background | Native tabs, expanders, metric cards, dataframes, and charts |
| Border | Native Streamlit component borders |
| Border radius | Native Streamlit component radius |
| Text — primary | `st.subheader`, `st.metric`, and dataframe headings |
| Text — secondary | `st.caption` for identity, timestamps, and scope |
| Spacing | Native columns plus expanders for secondary detail |
| Hover state | Native table, chart, tab, and expander states |
| Shadow | None |
| Accent usage | Primary full-width buttons only for execution actions |

**Pattern notes:** Show the small decision-making metric set first, then place configuration,
slices, document bodies, and case diagnostics inside native expanders. Use tables for exact records,
metric cards for headline values, and charts only for multi-metric comparison. Empty and unavailable
states remain native `st.info` and `st.error` messages. Charts must contain values with compatible
units; latency and cost stay outside unit-interval quality charts. Respect the session table-size
preference when rendering potentially long operational records.

### Source Registration Forms

File: ui/pages.py
Last updated: September 4, 2026

| Property | Class or pattern |
| --- | --- |
| Background | Native tab with source-type radio selection |
| Border | Native input and expander borders |
| Border radius | Native Streamlit component radius |
| Text — primary | Native form labels |
| Text — secondary | `st.caption` for counts and validation context |
| Spacing | One source workflow at a time with native vertical spacing |
| Hover state | Native uploader, input, radio, and button states |
| Shadow | None |
| Accent usage | Full-width primary button for the final ingestion action |

**Pattern notes:** Keep source-type workflows mutually exclusive, preview structured uploads before
ingestion, and disable final actions until required inputs are present. Advanced database controls
remain inside an expanded native panel and accept environment-variable references rather than
credentials.

### Retrieval Experiment Feedback

File: ui/pages.py and ui/renderers.py
Last updated: September 6, 2026

| Property | Class or pattern |
| --- | --- |
| Background | Native response body, captions, and diagnostic expanders |
| Border | Native Streamlit expander border |
| Border radius | Native Streamlit component radius |
| Text — primary | `st.write` for answer and ranked evidence content |
| Text — secondary | `st.caption` for total API time, scores, and routing time |
| Spacing | Native vertical response flow with secondary detail below the answer |
| Hover state | Native expander state |
| Shadow | None |
| Accent usage | Native informational and error feedback only |

**Pattern notes:** Display client-observed total API duration before the response diagnostics and
keep routing-only time within the routing expander. Citation, evidence, and routing expanders follow
the session diagnostic-expansion preference consistently. Hide unavailable retrieval modes and
disable invalid source-specific actions before submission.

### Next.js Workspace and Authentication

Files: `frontend/components/workspace.tsx`, `frontend/app/globals.css`
Last updated: September 7, 2026

| Property | Token or class |
| --- | --- |
| Background | `--canvas`, `--surface`, `--sidebar` from existing UI tokens |
| Border | `--border` for controls; `--border-soft` for cards |
| Radius | `--radius` (12px) cards; 8px buttons and inputs |
| Text | `--text`, `--secondary`, `--muted`; system UI font stack |
| Spacing | 24px card padding; 16px form gaps; 36px desktop page padding |
| Interaction | Primary `--accent`; visible 2px keyboard focus; disabled action states |
| Citations | `--citation`, `--citation-border`; expandable evidence with stable anchors |
| Shadow | Subtle composer shadow only |

Sidebar navigation uses icons with text and an explicit active state. Source types use the same
icons throughout uploads, tables, and selection controls. Empty, loading, error, and retry states
remain visible. Private originals are opened through short-lived, source-scoped download URLs.
The existing Streamlit interface retains its native styling for local development.
