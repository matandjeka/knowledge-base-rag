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
