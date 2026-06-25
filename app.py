import os
import asyncio
import json
import re
import aiohttp
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from agent import (
    extract_cities,
    choose_tool,
    call_mcp,
    clean_data,
    generate_city_insights,
    generate_general_response,
    generate_llm_text
)

app = FastAPI(title="MCP AI Agent 🌍")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================
# SELF-PING TO PREVENT SLEEP
# ==============================
async def self_ping():
    """Ping own health endpoint every 10 minutes to prevent Render free tier sleep."""
    await asyncio.sleep(30)  # wait for server startup
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                port = os.environ.get("PORT", "8000")
                async with session.get(f"http://localhost:{port}/health") as resp:
                    if resp.status == 200:
                        print("Self-ping: OK")
                    else:
                        print(f"Self-ping: Status {resp.status}")
        except Exception as e:
            print(f"Self-ping failed: {e}")
        await asyncio.sleep(600)  # 10 minutes


@app.on_event("startup")
async def startup_event():
    """Start background tasks on server startup."""
    asyncio.create_task(self_ping())


@app.get("/")
def home():
    return FileResponse("templates/index.html")


@app.post("/chat")
async def chat(request: Request):
    try:
        body = await request.json()
        user_input = body.get("message", "").strip()
        llm_enabled = body.get("llm_enabled", True)
        mcp_servers = body.get("mcp_servers", [])  # ← custom MCP servers from frontend

        if not user_input:
            return JSONResponse(
                status_code=400,
                content={"type": "error", "response": "❌ Empty message", "mcp_logs": []}
            )

        # Pass all MCP servers to process_query (it will handle default vs custom)
        result = await asyncio.to_thread(process_query, user_input, llm_enabled, mcp_servers)
        return result

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"type": "error", "response": f"❌ Server error: {str(e)}", "mcp_logs": []}
        )


def process_query(user_input: str, llm_enabled: bool, mcp_servers=None):
    all_logs = []
    cities = extract_cities(user_input)
    mcp_servers = mcp_servers or []

    # ======================
    # NO CITY FOUND
    # ======================
    if not cities:
        if llm_enabled:
            response_text = generate_general_response(user_input)
            return {
                "type": "text",
                "response": response_text,
                "mcp_logs": ["🤖 No city detected — routing to LLM for general answer"]
            }
        else:
            return {
                "type": "need_llm",
                "response": "I need a city name to fetch weather data. Please mention a city (e.g., 'Weather in Delhi').\n\nOr enable 🤖 LLM in Settings for AI-powered answers to general questions.",
                "mcp_logs": ["⚠️ No city found and LLM is disabled"]
            }

    # ======================
    # MULTI-CITY: Compare mode
    # ======================
    if len(cities) > 1:
        results = []
        for c in cities:
            r = call_mcp("getFullInsights", c, custom_url=None, server_config=None)
            all_logs.extend(r.get("logs", []))
            if "error" not in r:
                results.append(clean_data(r["data"]))

        if not results:
            return {
                "type": "error",
                "response": "❌ Could not fetch data for any of the requested cities.",
                "mcp_logs": all_logs
            }

        llm_text = ""
        if llm_enabled:
            compare_prompt = f"""Compare these cities based on the data:
{json.dumps(results, indent=2)}

Provide a brief comparison (2-3 sentences) highlighting key differences."""
            llm_text = generate_llm_text(compare_prompt)

        return {
            "type": "compare",
            "response": results,
            "llm_text": llm_text,
            "mcp_logs": all_logs
        }

    # ======================
    # SINGLE CITY: Default MCP (HUD) + Custom MCPs (text)
    # ======================
    city = cities[0]
    tool = choose_tool(user_input)

    # 1. ALWAYS call default weather MCP for HUD
    default_result = call_mcp(tool, city, custom_url=None, server_config=None)
    all_logs.extend(default_result.get("logs", []))
    hud_data = clean_data(default_result["data"]) if "error" not in default_result else None

    # 2. Call custom MCPs (non-default servers)
    custom_mcp_results = []
    for server in mcp_servers:
        if server.get("isDefault"):
            continue  # Skip default, already called above
        
        result = call_mcp(
            tool, 
            city, 
            custom_url=server["url"],
            server_config=server.get("config")
        )
        all_logs.extend(result.get("logs", []))
        if "error" not in result:
            raw_data = result["data"]
            # Flatten nested objects for metrics grid rendering
            flattened = {}
            for key, value in raw_data.items():
                if isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        if not sub_key.startswith('_') and sub_key != 'source':
                            flattened[sub_key] = sub_value
                elif not key.startswith('_') and key not in ['source', 'city', 'country', 'latitude', 'longitude']:
                    flattened[key] = value
            
            custom_mcp_results.append({
                "server_name": server["name"],
                "data": flattened,
                "format": result.get("format", "unknown")
            })

    # If default failed, return error
    if hud_data is None:
        return {
            "type": "error",
            "response": f"❌ {default_result.get('error', 'Default MCP failed')}",
            "mcp_logs": all_logs
        }

    # Build custom MCP text (LLM formatted or raw)
    custom_text = ""
    if custom_mcp_results:
        if llm_enabled:
            # --- STEP 1: Pre-filter external MCP results by relevance to user query ---
            def is_relevant_to_query(mcp_result, query):
                """Check if MCP data keys/values match user query keywords."""
                query_lower = query.lower()
                data = mcp_result.get("data", {})
                if not data:
                    return False
                # Extract keywords from query (ignore common stop words)
                stop_words = {
                    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or", "is", "are",
                    "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did",
                    "will", "would", "could", "should", "may", "might", "can", "shall", "me", "my",
                    "your", "we", "us", "our", "they", "them", "their", "it", "its", "this", "that",
                    "these", "those", "i", "you", "he", "she", "what", "which", "who", "when",
                    "where", "why", "how", "all", "any", "both", "each", "few", "more", "most",
                    "other", "some", "such", "no", "nor", "not", "only", "own", "same", "so",
                    "than", "too", "very", "just", "now", "then", "here", "there", "up", "down",
                    "out", "off", "over", "under", "again", "further", "once", "about", "into",
                    "through", "during", "before", "after", "above", "below", "between", "with",
                    "from", "by", "via", "show", "tell", "give", "get", "find", "list", "search",
                    "weather", "aqi", "air", "quality", "time", "coordinate", "temperature",
                    "city", "location", "data", "info", "information"
                }
                import re
                query_words = set(w.lower() for w in re.findall(r"[a-zA-Z]+", query_lower) if len(w) > 2 and w.lower() not in stop_words)
                if not query_words:
                    return True  # If no meaningful keywords, keep all

                # Check against all keys and string values in the MCP data
                all_text = " ".join(str(k) + " " + str(v) for k, v in data.items() if not k.startswith("_")).lower()
                # Also check server name
                all_text += " " + mcp_result.get("server_name", "").lower()

                # A result is relevant if at least one query word appears in the data text
                matches = sum(1 for w in query_words if w in all_text)
                # Require at least 1 match, or if query has only 1 word, require exact match
                return matches >= 1

            # Filter to only relevant MCP results
            relevant_mcp_results = [r for r in custom_mcp_results if is_relevant_to_query(r, user_input)]

            # Also keep results that have non-empty, non-weather data (heuristic: if keys are very different from weather)
            weather_keys = {"temperature", "weathercode", "is_day", "windspeed", "winddirection", "humidity", "pressure", "cloudcover", "precipitation", "visibility", "uv_index", "dew_point", "feels_like", "temp_min", "temp_max"}
            for r in custom_mcp_results:
                if r in relevant_mcp_results:
                    continue
                data = r.get("data", {})
                data_keys = set(k.lower() for k in data.keys() if not k.startswith("_"))
                # If data has keys that are NOT weather-related, it might be relevant
                non_weather_keys = data_keys - weather_keys
                if non_weather_keys and len(non_weather_keys) >= len(data_keys) * 0.3:
                    relevant_mcp_results.append(r)

            # Deduplicate while preserving order
            seen = set()
            deduped = []
            for r in relevant_mcp_results:
                rid = r.get("server_name", "") + json.dumps(r.get("data", {}), sort_keys=True)
                if rid not in seen:
                    seen.add(rid)
                    deduped.append(r)
            relevant_mcp_results = deduped

            # --- STEP 2: Send only relevant data to LLM ---
            if relevant_mcp_results:
                custom_prompt = f"""You are GeoBot, a location intelligence assistant.

The user asked: "{user_input}"

Relevant external MCP data:
{json.dumps(relevant_mcp_results, indent=2)}

Task: Answer the user's question using ONLY the Relevant external MCP data above.
- Write a brief, natural answer in plain flowing text citing specific details from the data.
- Do NOT mention weather, temperature, or general city info unless the user explicitly asked for it.
- Do NOT use bullet points, headers, or numbered lists. Max 100 words. No emojis unless the user used them."""
                custom_text = generate_llm_text(custom_prompt)
            else:
                custom_text = "(Note: No relevant data returned from connected MCP.)"
        else:
            # LLM disabled: frontend will render dropdown from custom_mcp_results
            custom_text = ""

    # If no custom MCPs configured, do NOT generate default LLM insights — HUD already shows weather data
    elif llm_enabled:
        custom_text = "(Note: No relevant data returned from connected MCP.)"

    # Merge custom MCP flat data into hud_data for unified rendering
    merged_hud = dict(hud_data)
    for custom in custom_mcp_results:
        for key, value in custom.get("data", {}).items():
            if key not in merged_hud and value is not None:
                merged_hud[key] = value

    return {
        "type": "hud_with_custom",
        "hud_data": merged_hud,
        "custom_text": custom_text,
        "custom_mcp_results": custom_mcp_results,
        "mcp_logs": all_logs
    }

@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)