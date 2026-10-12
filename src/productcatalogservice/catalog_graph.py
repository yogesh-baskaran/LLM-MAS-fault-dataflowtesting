"""
catalog_graph.py    LangGraph AI search graph for productcatalogservice

Key fixes vs the original:
  - LLM is initialised once at module level, not on every call
  - Uses ChatOllama (current API) instead of legacy OllamaLLM
  - Robust JSON extraction via regex so stray text doesn't break parsing
  - Proper error logging so you can see what goes wrong
  - OLLAMA_SERVICE_URL env-var so it works locally AND in K8s
"""

import json
import os
import re
import traceback
from typing import TypedDict, List

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END

# ---------------------------------------------------------------------------
# Configuration (override via environment variables)
# ---------------------------------------------------------------------------
CATALOG_PATH     = os.getenv("PRODUCT_CATALOG_JSON", "products.json")
OLLAMA_BASE_URL  = os.getenv("OLLAMA_SERVICE_URL",   "http://ollama-service:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL",          "mistral")

# ---------------------------------------------------------------------------
# 1. Load product catalogue once at startup
# ---------------------------------------------------------------------------
try:
    with open(CATALOG_PATH, "r") as f:
        ALL_PRODUCTS: List[dict] = json.load(f).get("products", [])
    print(f"[catalog_graph] Loaded {len(ALL_PRODUCTS)} products from {CATALOG_PATH}")
except Exception as e:
    print(f"[catalog_graph] FATAL  could not load catalogue: {e}")
    ALL_PRODUCTS = []

# ---------------------------------------------------------------------------
# 2. Initialise the LLM once (not on every request)
# ---------------------------------------------------------------------------
try:
    llm = ChatOllama(
        model=OLLAMA_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=0,          # deterministic output is better for JSON
        format="json",          # ask Ollama to return valid JSON directly
    )
    print(f"[catalog_graph] LLM ready: {OLLAMA_MODEL} @ {OLLAMA_BASE_URL}")
except Exception as e:
    print(f"[catalog_graph] FATAL  could not initialise LLM: {e}")
    llm = None

# ---------------------------------------------------------------------------
# 3. State definition
# ---------------------------------------------------------------------------
class State(TypedDict):
    query:   str
    results: List[dict]

# ---------------------------------------------------------------------------
# 4. Helper  extract a JSON list robustly from raw LLM text
# ---------------------------------------------------------------------------
def _extract_json_list(text: str) -> List[str]:
    """
    Tries several strategies to get a list of strings out of raw LLM output.

    Handles common LLM responses such as:
      ["OLJCESPC7Z", "66VCHSJNUP"]
      ```json\n["OLJCESPC7Z"]\n```
      {"ids": ["OLJCESPC7Z"]}
      Here are the matching IDs: ["OLJCESPC7Z"]
    """
    # Strip code fences
    cleaned = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

    # Strategy 1  the whole response is a list
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return [str(i) for i in parsed]
        # Strategy 2  {"ids": [...]} or {"results": [...]} wrapper
        for key in ("ids", "results", "product_ids", "matches"):
            if key in parsed and isinstance(parsed[key], list):
                return [str(i) for i in parsed[key]]
    except json.JSONDecodeError:
        pass

    # Strategy 3  find first [...] substring
    m = re.search(r"\[.*?\]", cleaned, re.DOTALL)
    if m:
        try:
            return [str(i) for i in json.loads(m.group())]
        except json.JSONDecodeError:
            pass

    # Strategy 4  extract quoted strings that look like product IDs
    return re.findall(r'"([A-Z0-9]{8,})"', cleaned)

# ---------------------------------------------------------------------------
# 5. Search node
# ---------------------------------------------------------------------------
def search_node(state: State) -> State:
    if llm is None:
        print("[search_node] LLM not available, returning empty results")
        return {"results": []}

    # Slim catalogue summary so it fits in the context window
    catalog_summary = [
        {
            "id":          p["id"],
            "name":        p["name"],
            "description": p.get("description", ""),
            "categories":  p.get("categories", []),
        }
        for p in ALL_PRODUCTS
    ]

    prompt = f"""You are a product search assistant.

User query: "{state['query']}"

Product catalogue (JSON):
{json.dumps(catalog_summary, indent=2)}

Task: Return a JSON array of product IDs whose name, description or categories
best match the user query. If nothing matches, return an empty array.

Rules:
- Return ONLY a valid JSON array of ID strings, e.g. ["OLJCESPC7Z", "1YMWWN1N4O"]
- Do NOT include any explanation or markdown.
- Do NOT invent IDs that are not in the catalogue above.
"""

    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        raw_text = response.content if hasattr(response, "content") else str(response)
        print(f"[search_node] Raw LLM response: {raw_text!r}")

        matched_ids = _extract_json_list(raw_text)
        print(f"[search_node] Matched IDs: {matched_ids}")

        results = [p for p in ALL_PRODUCTS if p["id"] in matched_ids]
        print(f"[search_node] Returning {len(results)} products")
        return {"results": results}

    except Exception:
        # Print the FULL traceback so you can see exactly what failed
        print(f"[search_node] Exception:\n{traceback.format_exc()}")
        return {"results": []}

# ---------------------------------------------------------------------------
# 6. Build and compile the graph
# ---------------------------------------------------------------------------
_workflow = StateGraph(State)
_workflow.add_node("search", search_node)
_workflow.add_edge(START, "search")
_workflow.add_edge("search", END)

graph = _workflow.compile()
print("[catalog_graph] Graph compiled successfully")
