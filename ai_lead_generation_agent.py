import streamlit as st
import os
import google.generativeai as genai
import requests
from firecrawl import FirecrawlApp
from pydantic import BaseModel, Field
from typing import List, Optional
import json
import re
import logging
logger = logging.getLogger("leadai")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)
from composio_phidata import Action, ComposioToolSet

class QuoraUserInteractionSchema(BaseModel):
    username: str = Field(description="The username of the user who posted the question or answer")
    bio: str = Field(description="The bio or description of the user")
    post_type: str = Field(description="The type of post, either 'question' or 'answer'")
    timestamp: str = Field(description="When the question or answer was posted")
    upvotes: int = Field(default=0, description="Number of upvotes received")
    links: List[str] = Field(default_factory=list, description="Any links included in the post")

class QuoraPageSchema(BaseModel):
    interactions: List[QuoraUserInteractionSchema] = Field(description="List of all user interactions (questions and answers) on the page")

def search_for_urls(company_description: str, firecrawl_api_key: str, num_links: int) -> List[str]:
    url = "https://api.firecrawl.dev/v1/search"
    headers = {
        "Authorization": f"Bearer {firecrawl_api_key}",
        "Content-Type": "application/json"
    }
    query1 = f"quora websites where people are looking for {company_description} services"
    payload = {
        "query": query1,
        "limit": num_links,
        "lang": "en",
        "location": "United States",
        "timeout": 60000,
    }
    response = requests.post(url, json=payload, headers=headers)
    if response.status_code == 200:
        data = response.json()
        if data.get("success"):
            results = data.get("data", [])
            return [result["url"] for result in results]
    return []

def extract_user_info_from_urls(urls: List[str], firecrawl_api_key: str) -> List[dict]:
    user_info_list = []
    firecrawl_app = FirecrawlApp(api_key=firecrawl_api_key)
    
    try:
        for url in urls:
            response = firecrawl_app.extract(
                [url],
                {
                    'prompt': 'Extract all user information including username, bio, post type (question/answer), timestamp, upvotes, and any links from Quora posts. Focus on identifying potential leads who are asking questions or providing answers related to the topic.',
                    'schema': QuoraPageSchema.model_json_schema(),
                }
            )
            
            if response.get('success') and response.get('status') == 'completed':
                interactions = response.get('data', {}).get('interactions', [])
                if interactions:
                    user_info_list.append({
                        "website_url": url,
                        "user_info": interactions
                    })
    except Exception:
        pass
    
    return user_info_list

def format_user_info_to_flattened_json(user_info_list: List[dict]) -> List[dict]:
    flattened_data = []
    
    for info in user_info_list:
        website_url = info["website_url"]
        user_info = info["user_info"]
        
        for interaction in user_info:
            flattened_interaction = {
                "Website URL": website_url,
                "Username": interaction.get("username", ""),
                "Bio": interaction.get("bio", ""),
                "Post Type": interaction.get("post_type", ""),
                "Timestamp": interaction.get("timestamp", ""),
                "Upvotes": interaction.get("upvotes", 0),
                "Links": ", ".join(interaction.get("links", [])),
            }
            flattened_data.append(flattened_interaction)
    
    return flattened_data

def _to_jsonable(obj: object) -> object:
    """Best-effort conversion of arbitrary objects to JSON-like structures for inspection."""
    try:
        # Pass through primitives, dicts and lists
        if obj is None or isinstance(obj, (str, int, float, bool, dict, list)):
            return obj
        # pydantic/BaseModel style
        if hasattr(obj, "model_dump") and callable(obj.model_dump):
            return obj.model_dump()  # type: ignore
        if hasattr(obj, "dict") and callable(obj.dict):
            return obj.dict()  # type: ignore
        # dataclass-like
        try:
            from dataclasses import asdict, is_dataclass
            if is_dataclass(obj):
                return asdict(obj)
        except Exception:
            pass
        # generic object attributes
        if hasattr(obj, "__dict__"):
            return dict(obj.__dict__)
    except Exception:
        pass
    return obj


def _extract_google_sheet_url_from_any(obj: object) -> Optional[str]:
    """Try multiple strategies to extract a Google Sheets URL from any object."""
    # 1. If dict-like, look for common fields recursively
    try:
        obj = _to_jsonable(obj)
        if isinstance(obj, dict):
            # Construct from spreadsheetId if available
            sid = obj.get("spreadsheetId") or obj.get("spreadsheet_id")
            if isinstance(sid, str) and sid:
                return f"https://docs.google.com/spreadsheets/d/{sid}"
            # direct fields
            for key in (
                "spreadsheetUrl",
                "spreadsheetURL",
                "url",
                "sheet_url",
                "sheetUrl",
                "document_url",
                "link",
                "webViewLink",
            ):
                val = obj.get(key)
                if isinstance(val, str) and "docs.google.com/spreadsheets/d/" in val:
                    return val
            # nested search
            for v in obj.values():
                found = _extract_google_sheet_url_from_any(v)
                if found:
                    return found
        elif isinstance(obj, list):
            for it in obj:
                found = _extract_google_sheet_url_from_any(it)
                if found:
                    return found
    except Exception:
        pass

    # 2. Fallback: stringify and regex
    try:
        text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    except Exception:
        text = str(obj)
    m = re.search(r"https://docs\.google\.com/spreadsheets/d/[\w-]+", text)
    if m:
        return m.group(0)
    # 3. Fallback: find spreadsheetId in text and construct URL
    m2 = re.search(r"spreadsheetId\"?\s*[:=]\s*\"([\w-]+)\"", text)
    if m2:
        return f"https://docs.google.com/spreadsheets/d/{m2.group(1)}"
    return None


def write_to_google_sheets_via_composio(flattened_data: List[dict], composio_api_key: str, title: Optional[str] = None, debug: bool = False) -> Optional[str]:
    """Create a Google Sheet from JSON via Composio directly (no LLM)."""
    try:
        if not flattened_data:
            if debug:
                st.warning("No data to write to Google Sheets.")
            return None
        toolset = ComposioToolSet(api_key=composio_api_key)
        toolkit = toolset.get_tools(actions=[Action.GOOGLESHEETS_SHEET_FROM_JSON])[0]
        logger.info("Composio toolkit acquired: type=%s attrs=%s", type(toolkit).__name__, [a for a in dir(toolkit) if not a.startswith('_')][:30])

        # Prefer an explicit title; Composio often accepts "title" + "data" as list[dict]
        payload_options = [
            {"title": title or "AI Leads", "data": flattened_data},
            {"title": title or "AI Leads", "rows": flattened_data},
            {"title": title or "AI Leads", "json": flattened_data},
            {"data": flattened_data},
        ]

        result = None
        used_payload = None
        for payload in payload_options:
            try:
                logger.info("Calling Composio GOOGLESHEETS_SHEET_FROM_JSON with keys=%s", list(payload.keys()))
                # Preferred signature
                if hasattr(toolkit, "run"):
                    try:
                        result = toolkit.run(action=Action.GOOGLESHEETS_SHEET_FROM_JSON, params=payload)
                    except TypeError:
                        # Some versions may take (action, params)
                        result = toolkit.run(Action.GOOGLESHEETS_SHEET_FROM_JSON, payload)
                # Fallbacks
                if result is None and hasattr(toolkit, "run_action"):
                    result = toolkit.run_action(Action.GOOGLESHEETS_SHEET_FROM_JSON, payload)
                if result is None and hasattr(toolkit, "invoke"):
                    try:
                        result = toolkit.invoke(action=Action.GOOGLESHEETS_SHEET_FROM_JSON, params=payload)
                    except Exception:
                        pass
                if result is None and hasattr(toolkit, "execute"):
                    try:
                        result = toolkit.execute(action=Action.GOOGLESHEETS_SHEET_FROM_JSON, params=payload)
                    except Exception:
                        pass
                # Last resort: if toolkit exposes a 'tools' collection
                if result is None and hasattr(toolkit, "tools"):
                    try:
                        for t in getattr(toolkit, "tools"):
                            t_name = getattr(t, "name", "")
                            if isinstance(t_name, str) and "GOOGLESHEETS" in t_name.upper():
                                if hasattr(t, "run"):
                                    result = t.run(**payload)
                                    break
                                if callable(t):
                                    result = t(**payload)
                                    break
                    except Exception:
                        pass
                if result:
                    used_payload = payload
                    break
            except Exception as e:
                logger.exception("Composio call failed for payload keys=%s", list(payload.keys()))
                continue

        if not result:
            return None

        # Extract URL robustly
        url = _extract_google_sheet_url_from_any(result)
        if url:
            logger.info("Parsed Google Sheets URL: %s", url)
            return url
        # As a last resort, stringify and search
        try:
            text = result if isinstance(result, str) else json.dumps(result, default=str)
        except Exception:
            text = str(result)
        url = _extract_google_sheet_url_from_any(text)
        if url:
            logger.info("Parsed Google Sheets URL from text: %s", url)
            return url
        if debug:
            st.warning("Could not parse a Google Sheets link from Composio response. See raw response below.")
            with st.expander("Raw Composio response"):
                st.write({
                    "used_payload_keys": list(used_payload.keys()) if used_payload else None,
                    "result_type": type(result).__name__,
                    "result_preview": str(result)[:1000],
                })
                st.write(result)
        # Extra terminal diagnostics
        try:
            attrs = [a for a in dir(result) if not a.startswith("_")]
        except Exception:
            attrs = []
        logger.warning(
            "Failed to parse Google Sheets link; result_type=%s attrs=%s preview=%s",
            type(result).__name__, attrs[:50], str(result)[:500]
        )
        return None
    except Exception as e:
        logger.exception("Error while writing to Google Sheets via Composio")
        if debug:
            st.exception(e)
        return None

PROMPT_TRANSFORM_INSTRUCTIONS = (
    "You are an expert at transforming detailed user queries into concise company descriptions.\n"
    "Your task is to extract the core business/product focus in 3-4 words.\n\n"
    "Examples:\n"
    "Input: \"Generate leads looking for AI-powered customer support chatbots for e-commerce stores.\"\n"
    "Output: \"AI customer support chatbots for e commerce\"\n\n"
    "Input: \"Find people interested in voice cloning technology for creating audiobooks and podcasts\"\n"
    "Output: \"voice cloning technology\"\n\n"
    "Input: \"Looking for users who need automated video editing software with AI capabilities\"\n"
    "Output: \"AI video editing software\"\n\n"
    "Input: \"Need to find businesses interested in implementing machine learning solutions for fraud detection\"\n"
    "Output: \"ML fraud detection\"\n\n"
    "Always focus on the core product/service and keep it concise but clear."
)

def get_secret(name: str) -> str:
    """Fetch a secret from Streamlit secrets (top-level or [default]) or env vars."""
    try:
        # Top-level
        if name in st.secrets:
            return str(st.secrets[name])
        # [default] table support
        if "default" in st.secrets and name in st.secrets["default"]:
            return str(st.secrets["default"][name])
    except Exception:
        pass
    return os.environ.get(name, "")

def _parse_gemini_keys(raw: str) -> List[str]:
    keys: List[str] = []
    # 1) UI-provided raw
    if raw:
        parts = [p.strip() for p in raw.split(",")]
        keys.extend([p for p in parts if p])
    # 2) Secrets-provided comma-separated
    raw_from_secrets = get_secret("GEMINI_API_KEYS")
    if raw_from_secrets:
        parts2 = [p.strip() for p in raw_from_secrets.split(",")]
        keys.extend([p for p in parts2 if p])
    # 3) Separate indexed keys
    for i in range(1, 4):
        v = get_secret(f"GEMINI_API_KEY_{i}")
        if v:
            keys.append(v)
    # Deduplicate while preserving order
    seen = set()
    uniq = []
    for k in keys:
        if k not in seen:
            uniq.append(k)
            seen.add(k)
    return uniq

def transform_with_gemini(api_keys: List[str], user_query: str) -> str:
    if not api_keys:
        raise ValueError("No Gemini API keys provided")
    # Round-robin rotation persisted in session
    idx_key = "gemini_key_index"
    if idx_key not in st.session_state:
        st.session_state[idx_key] = 0
    idx = st.session_state[idx_key] % len(api_keys)
    st.session_state[idx_key] = (st.session_state[idx_key] + 1) % max(1, len(api_keys))

    genai.configure(api_key=api_keys[idx])
    model = genai.GenerativeModel("gemini-2.5-pro")
    prompt = f"{PROMPT_TRANSFORM_INSTRUCTIONS}\n\nInput: \"{user_query}\"\nOutput:"
    resp = model.generate_content(prompt)
    text = (resp.text or "").strip() if hasattr(resp, "text") else ""
    # Ensure short output
    if len(text.split()) > 8:
        text = " ".join(text.split()[:8])
    return text

def main():
    st.title("🎯 AI Lead Generation Agent")
    st.info("This firecrawl powered agent helps you generate leads from Quora by searching for relevant posts and extracting user information.")

    with st.sidebar:
        st.header("API Keys")
        # Prefer Streamlit secrets (top-level or [default]); fallback to env; allow UI override
        firecrawl_api_key_default = get_secret("FIRECRAWL_API_KEY")
        composio_api_key_default = get_secret("COMPOSIO_API_KEY")
        gemini_keys_raw_default = get_secret("GEMINI_API_KEYS")

        firecrawl_api_key = st.text_input("Firecrawl API Key", value=firecrawl_api_key_default, type="password")
        st.caption("Get your Firecrawl API key from [Firecrawl's website](https://www.firecrawl.dev/app/api-keys)")

        gemini_keys_raw = st.text_input("Gemini API Keys (comma-separated)", value=gemini_keys_raw_default, type="password")
        st.caption("Provide 1–3 Gemini API keys; they will be rotated per request. Get keys from Google AI Studio.")

        composio_api_key = st.text_input("Composio API Key", value=composio_api_key_default, type="password")
        st.caption("Provide your Composio API key with Google Sheets integration enabled.")

        num_links = st.number_input("Number of links to search", min_value=1, max_value=25, value=10)

        sheet_title = st.text_input("Google Sheet title (optional)", value="AI Leads")
        show_debug = st.checkbox("Show Composio debug output", value=False)

        if st.button("Reset"):
            st.session_state.clear()
            st.experimental_rerun()

    user_query = st.text_area(
        "Describe what kind of leads you're looking for:",
        placeholder="e.g., Looking for users who need automated video editing software with AI capabilities",
        help="Be specific about the product/service and target audience. The AI will convert this into a focused search query."
    )

    if st.button("Generate Leads"):
        gemini_keys = _parse_gemini_keys(gemini_keys_raw)

        if not firecrawl_api_key:
            st.error("Firecrawl API key is required.")
            return
        if not gemini_keys:
            st.error("At least one Gemini API key is required.")
            return
        if not composio_api_key:
            st.error("Composio API key is required.")
            return
        if not user_query:
            st.error("Describe what kind of leads you're looking for.")
            return
        else:
            with st.spinner("Processing your query..."):
                try:
                    concise = transform_with_gemini(gemini_keys, user_query)
                except Exception as e:
                    st.error(f"Gemini transform failed: {e}")
                    return
                st.write("🎯 Searching for:", concise)
            
            with st.spinner("Searching for relevant URLs..."):
                urls = search_for_urls(concise, firecrawl_api_key, num_links)
            
            if urls:
                st.subheader("Quora Links Used:")
                for url in urls:
                    st.write(url)
                
                with st.spinner("Extracting user info from URLs..."):
                    user_info_list = extract_user_info_from_urls(urls, firecrawl_api_key)
                
                with st.spinner("Formatting user info..."):
                    flattened_data = format_user_info_to_flattened_json(user_info_list)

                google_sheets_link: Optional[str] = None
                if not flattened_data:
                    st.warning("No interactions found to write. Try increasing the number of links or refining the query.")
                else:
                    with st.spinner("Writing to Google Sheets via Composio..."):
                        google_sheets_link = write_to_google_sheets_via_composio(
                            flattened_data,
                            composio_api_key,
                            title=sheet_title.strip() or None,
                            debug=show_debug,
                        )
                
                if google_sheets_link:
                    st.success("Lead generation and data writing to Google Sheets completed successfully!")
                    st.subheader("Google Sheets Link:")
                    st.markdown(f"[View Google Sheet]({google_sheets_link})")
                else:
                    st.error("Failed to retrieve the Google Sheets link.")
                    # Fallback: offer CSV download
                    try:
                        import io, csv
                        csv_buf = io.StringIO()
                        if flattened_data:
                            fieldnames = list(flattened_data[0].keys())
                            writer = csv.DictWriter(csv_buf, fieldnames=fieldnames)
                            writer.writeheader()
                            for row in flattened_data:
                                writer.writerow(row)
                            st.download_button(
                                label="Download CSV",
                                data=csv_buf.getvalue(),
                                file_name=(sheet_title.strip() or "AI Leads").replace(" ", "_") + ".csv",
                                mime="text/csv",
                            )
                            st.info("CSV provided as a fallback while we refine the Sheets link parsing.")
                    except Exception:
                        pass
            else:
                st.warning("No relevant URLs found.")

if __name__ == "__main__":
    main()
