import os
import asyncio
import json
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
            # Send custom MCP data to LLM for formatting with relevance check
            custom_prompt = f"""You are GeoBot, a location intelligence assistant.

The user asked: "{user_input}"

Default weather/location data for {hud_data.get('city', 'this city')}:
{json.dumps(hud_data, indent=2)}

External MCP data:
{json.dumps(custom_mcp_results, indent=2)}

Task: Answer the user's question directly.
- If the External MCP data above is relevant to "{user_input}", use it as the primary source for your answer and cite specific details.
- If the External MCP data is NOT relevant (e.g., user asked for hospitals but MCP returned weather or nothing useful), answer based on the default weather data (if relevant to the question) or your own general knowledge. End your response with this exact note: (Note: No relevant data returned from connected MCP.)
- Do NOT use bullet points, headers, or numbered lists. Write in plain flowing text like a friendly assistant. Max 100 words. No emojis unless the user used them."""
            custom_text = generate_llm_text(custom_prompt)
        else:
            # LLM disabled: frontend will render dropdown from custom_mcp_results
            custom_text = ""

    # If no custom MCPs, use default LLM insights on weather only
    elif llm_enabled:
        custom_text = generate_city_insights(user_input, hud_data)

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