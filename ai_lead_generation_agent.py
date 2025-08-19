import streamlit as st
import os
import google.generativeai as genai
import requests
from firecrawl import FirecrawlApp
from pydantic import BaseModel, Field
from typing import List, Optional
import json
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

def write_to_google_sheets_via_composio(flattened_data: List[dict], composio_api_key: str, title: Optional[str] = None) -> Optional[str]:
    """Create a Google Sheet from JSON via Composio directly (no LLM)."""
    try:
        toolset = ComposioToolSet(api_key=composio_api_key)
        tool = toolset.get_tools(actions=[Action.GOOGLESHEETS_SHEET_FROM_JSON])[0]

        payload_options = [
            {"title": title or "AI Leads", "data": flattened_data},
            {"title": title or "AI Leads", "json": flattened_data},
            {"title": title or "AI Leads", "rows": flattened_data},
            {"data": flattened_data},
        ]

        result = None
        for payload in payload_options:
            try:
                if hasattr(tool, "run"):
                    result = tool.run(**payload)
                elif callable(tool):
                    result = tool(**payload)
                else:
                    result = tool.run(payload) if hasattr(tool, "run") else tool(payload)  # type: ignore
                if result:
                    break
            except Exception:
                continue

        if not result:
            return None

        text = None
        if isinstance(result, str):
            text = result
        elif hasattr(result, "content"):
            text = str(result.content)
        elif isinstance(result, dict):
            text = json.dumps(result)
        else:
            text = str(result)

        if text and "https://docs.google.com/spreadsheets/d/" in text:
            link_part = text.split("https://docs.google.com/spreadsheets/d/")[1].split()[0]
            return f"https://docs.google.com/spreadsheets/d/{link_part}"
        return None
    except Exception:
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

def _parse_gemini_keys(raw: str) -> List[str]:
    keys = []
    if raw:
        parts = [p.strip() for p in raw.split(",")]
        keys = [p for p in parts if p]
    # Fallback: separate indexed keys
    for i in range(1, 4):
        v = os.environ.get(f"GEMINI_API_KEY_{i}", "")
        if not v:
            try:
                v = str(st.secrets.get(f"GEMINI_API_KEY_{i}", ""))
            except Exception:
                v = ""
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
        # Prefer Streamlit secrets; fallback to environment variables; allow UI override
        def _get_secret(name: str) -> str:
            try:
                if name in st.secrets:
                    return str(st.secrets[name])
            except Exception:
                pass
            return os.environ.get(name, "")

        firecrawl_api_key_default = _get_secret("FIRECRAWL_API_KEY")
    composio_api_key_default = _get_secret("COMPOSIO_API_KEY")
        gemini_keys_raw_default = _get_secret("GEMINI_API_KEYS")

        firecrawl_api_key = st.text_input("Firecrawl API Key", value=firecrawl_api_key_default, type="password")
        st.caption(" Get your Firecrawl API key from [Firecrawl's website](https://www.firecrawl.dev/app/api-keys)")
    gemini_keys_raw = st.text_input("Gemini API Keys (comma-separated)", value=gemini_keys_raw_default, type="password")
    st.caption(" Provide 1–3 Gemini API keys; they will be rotated per request. Get keys from Google AI Studio.")
    composio_api_key = st.text_input("Composio API Key", value=composio_api_key_default, type="password")
    st.caption(" Provide your Composio API key with Google Sheets integration enabled.")
        
        num_links = st.number_input("Number of links to search", min_value=1, max_value=10, value=3)
        
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
                
                with st.spinner("Writing to Google Sheets via Composio..."):
                    google_sheets_link = write_to_google_sheets_via_composio(flattened_data, composio_api_key)
                
                if google_sheets_link:
                    st.success("Lead generation and data writing to Google Sheets completed successfully!")
                    st.subheader("Google Sheets Link:")
                    st.markdown(f"[View Google Sheet]({google_sheets_link})")
                else:
                    st.error("Failed to retrieve the Google Sheets link.")
            else:
                st.warning("No relevant URLs found.")

if __name__ == "__main__":
    main()
