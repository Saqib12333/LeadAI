# AI-Lead-Generation-Agent — Working Instructions (Living Doc)

This is a single source of truth for humans and AI agents working on this repo. Keep it updated with every meaningful change to code, dependencies, or workflow.

Last updated: 2025-08-20
Repository: GURPREETKAURJETHRA/AI-Lead-Generation-Agent (branch: main)
Primary entrypoint: `ai_lead_generation_agent.py` (Streamlit app)
License: MIT

## Goal and scope

An AI agent that finds and qualifies leads from Quora and writes them to Google Sheets. It:
- Transforms a free-text lead request into a concise description
- Searches Quora via Firecrawl Search API
- Extracts structured user interactions via Firecrawl Extract API (using a Pydantic schema)
- Flattens results
- Creates a Google Sheet from JSON via Composio + phidata tool orchestration

## Architecture overview

- UI: Streamlit (single-page app) — `ai_lead_generation_agent.py`
- Prompt transform LLM: Google Gemini `gemini-2.5-pro` (via google-generativeai) with API key rotation
- Sheets write: Direct Composio Google Sheets action invocation (no OpenAI, no MCP)
- Data sources: Quora pages discovered by Firecrawl search
- Extraction: Firecrawl Extract endpoint with `QuoraPageSchema`
- Persistence: Google Sheets via Composio tool `GOOGLESHEETS_SHEET_FROM_JSON`
- Types: Pydantic models for extraction schema and safety

Data flow:
1) User enters a lead query in the UI
2) `transform_with_gemini` converts it to a concise 3–4 word company description (Gemini with key rotation)
3) `search_for_urls` calls Firecrawl Search (REST) to find relevant Quora URLs
4) `extract_user_info_from_urls` uses Firecrawl Extract (SDK) with `QuoraPageSchema` to get interactions
5) `format_user_info_to_flattened_json` converts nested interactions to rows
6) Google Sheets write step: direct Composio call to create a sheet from JSON

## Tech stack and pinned deps

From `requirements.txt`:
- firecrawl-py==1.9.0
 - phidata==2.7.3  (currently unused; safe to remove in future cleanup)
 - composio-phidata==0.6.15
- composio==0.1.1
- pydantic==2.10.5
- streamlit (unpinned)
 - google-generativeai==0.7.2

Recommend Python 3.10 or 3.11.

## Prerequisites

- Python 3.10+ installed
- Accounts and API keys:
  - Firecrawl API key: https://www.firecrawl.dev/app/api-keys
  - Gemini API keys (1–3): Google AI Studio
  - Composio API key: https://composio.ai (Google Sheets integration must be active)
- Composio Google Sheets connection:
  - Run: `composio add googlesheets`
  - In Composio dashboard, authorize Google Sheets and ensure it’s active under connections

## Local setup (Windows, PowerShell)

- Clone and create a virtual environment
  - `git clone https://github.com/GURPREETKAURJETHRA/AI-Lead-Generation-Agent.git`
  - `cd AI-Lead-Generation-Agent`
  - `python -m venv .venv`
  - `.\.venv\Scripts\Activate.ps1`
- Install dependencies
  - `pip install -r requirements.txt`
- Configure Composio Google Sheets
  - `composio add googlesheets`
  - Complete OAuth in dashboard and verify the integration is active

## Running the app

- Start Streamlit
  - `streamlit run ai_lead_generation_agent.py`
- In the sidebar, provide:
  - Firecrawl API Key
  - Gemini API Keys (comma-separated) — rotated per request
  - Composio API Key (for Google Sheets)
- Choose number of links (1–10) and click “Generate Leads”.
- If successful, a Google Sheets link is shown.

Notes:
- Keys are entered via UI; they are not read from environment variables in the current code.
- The OpenAI model used is `gpt-4o-mini` via phidata’s `OpenAIChat` wrapper.

## Key files and functions

- `ai_lead_generation_agent.py`
  - Schemas
    - `QuoraUserInteractionSchema`: username, bio, post_type, timestamp, upvotes, links
    - `QuoraPageSchema`: interactions: List[QuoraUserInteractionSchema]
  - Functions (contract summary)
    - `search_for_urls(company_description: str, firecrawl_api_key: str, num_links: int) -> List[str]`
      - Calls Firecrawl Search REST. Returns list of URLs (or empty list). Handles 200/success only.
    - `extract_user_info_from_urls(urls: List[str], firecrawl_api_key: str) -> List[dict]`
      - Uses `FirecrawlApp.extract` with prompt + JSON schema. Returns list of { website_url, user_info } entries. Swallows exceptions.
    - `format_user_info_to_flattened_json(user_info_list: List[dict]) -> List[dict]`
      - Flattens nested interactions into row dicts for Sheets.
    - `write_to_google_sheets_via_composio(flattened_data: List[dict], composio_api_key: str, title: Optional[str] = None) -> Optional[str]`
      - Direct call to Composio Google Sheets action; attempts common payload shapes and returns sheet URL if found.
    - `transform_with_gemini(api_keys: List[str], user_query: str) -> str`
      - Performs prompt transformation using Gemini with per-request key rotation.
    - `main()`
      - Streamlit UI and end-to-end flow.

## Configuration and secrets

- You can provide secrets via any of the following, in priority order:
  1) Streamlit secrets (`.streamlit/secrets.toml`):
    - `FIRECRAWL_API_KEY`
    - `GEMINI_API_KEYS` (comma-separated) or `GEMINI_API_KEY_1..3`
    - `COMPOSIO_API_KEY`
  2) Environment variables: same names as above
  3) Sidebar inputs (override what’s prefilled)
- Do not commit secrets. Prefer local `.streamlit/secrets.toml` or user-specific env vars.
- Example `.streamlit/secrets.toml`:

  [default]
  FIRECRAWL_API_KEY = "..."
  GEMINI_API_KEYS = "..."  # or use GEMINI_API_KEY_1..3
  GEMINI_API_KEY_1 = "..."
  GEMINI_API_KEY_2 = "..."
  GEMINI_API_KEY_3 = "..."
  COMPOSIO_API_KEY = "..."
  
  Note: `.streamlit/secrets.toml` is gitignored in this repo to avoid leaking keys.

## Edge cases and error handling

- No URLs found: shows a warning and stops.
- Firecrawl search/extract failures: functions return empty data; exceptions in extract are suppressed.
- Google Sheets creation failures: returns `None`; UI shows an error.
- Rate limits/timeouts: Firecrawl timeout is set to 60 seconds in search payload; extract relies on SDK behavior.
- Link parsing: `write_to_google_sheets_via_composio` searches for a Google Sheets URL in the action output.

## Troubleshooting

- `ModuleNotFoundError`: Re-activate venv; reinstall `pip install -r requirements.txt`.
- Composio tool errors: Ensure `composio add googlesheets` and connection is active in dashboard.
- Firecrawl 401/403: Verify API key and quota.
- Gemini errors: Verify keys, model name `gemini-2.5-pro`, and quota in Google AI Studio.
- Empty output: Try increasing number of links or refining query; check Firecrawl status.

## Development guidelines

- Maintain type hints; prefer small, pure functions with minimal side-effects.
- Keep dependencies pinned where possible; align with `requirements.txt`.
- Log minimally in Streamlit; avoid printing secrets.
- Prefer Pydantic models for structured data exchanged with external APIs.
- When changing behavior, add a small sample input/output in this doc or tests.

## Testing suggestions (lightweight)

- Unit-test `format_user_info_to_flattened_json` with a synthetic `user_info_list`.
- Consider a tiny integration shim for Firecrawl search using a mock or recorded response.
- No formal test harness exists yet; contributions welcome.

## Updating this document (policy)

- Update this file in the same PR as any code or dependency change that affects:
  - Setup, running, configuration, dependencies, or behavior
- Add an entry to the changelog below
- Commit message should include `[docs]` when only doc changes are made

## Changelog

- 2025-08-20: Initial creation of copilot-instructions.md
- 2025-08-20: Added env/Streamlit secrets fallback for API keys; documented variable names
- 2025-08-20: Added `.streamlit/secrets.toml` template locally and gitignored it
 - 2025-08-20: Switched prompt transform to Gemini with API key rotation; removed MCP/OpenAI paths; direct Composio Sheets call; updated deps and docs

## License and attribution

- MIT License — see `LICENSE`
- Images under `IMG_AILG/` are used in README for illustration
