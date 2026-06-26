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
        mcp_enabled = body.get("mcp_enabled", False)
        mcp_servers = body.get("mcp_servers", [])  # ← custom MCP servers from frontend

        if not user_input:
            return JSONResponse(
                status_code=400,
                content={"type": "error", "response": "❌ Empty message", "mcp_logs": []}
            )

        # Pass all MCP servers to process_query (it will handle default vs custom)
        result = await asyncio.to_thread(process_query, user_input, llm_enabled, mcp_servers, mcp_enabled)
        return result

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"type": "error", "response": f"❌ Server error: {str(e)}", "mcp_logs": []}
        )


def process_query(user_input: str, llm_enabled: bool, mcp_servers=None, mcp_master_enabled=False):
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
    # CHECK IF MASTER MCP IS ENABLED
    # ======================
    if not mcp_master_enabled:
        # Master MCP toggle is OFF — tell user to enable it
        return {
            "type": "need_mcp",
            "response": "🔌 MCP is disabled.<br><br>To get weather, AQI, and location data, please enable MCP:<br><br>1. Click ⚙️ Settings (top-left)<br>2. Toggle ON the 🌐 MCP Server switch<br><br>The default weather MCP will connect automatically.",
            "mcp_logs": ["⚠️ Master MCP toggle is OFF"]
        }

    # ======================
    # MULTI-CITY: Compare mode
    # ======================
    if len(cities) > 1:
        results = []
        for c in cities:
            # Use default MCP (from agent.py)
            r = call_mcp("getFullInsights", c)
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
    # SINGLE CITY: Call enabled MCPs
    # ======================
    city = cities[0]
    tool = choose_tool(user_input)

    # 1. Call DEFAULT MCP (from agent.py) — this is the master/primary MCP
    default_result = call_mcp(tool, city)  # No custom_url = uses DEFAULT_MCP_URL
    all_logs.extend(default_result.get("logs", []))
    
    default_data = None
    if "error" not in default_result:
        default_data = clean_data(default_result["data"])

    # 2. Call any enabled CUSTOM MCP servers (extras beyond default)
    custom_mcp_results = []
    enabled_custom_servers = [s for s in mcp_servers if s.get("enabled") == True and not s.get("isDefault")]
    for server in enabled_custom_servers:
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

    if default_data is None:
        return {
            "type": "error",
            "response": f"❌ Default MCP failed to return data for {city}.",
            "mcp_logs": all_logs
        }

    # Use default result as primary HUD data
    primary_data = default_data

    # Build LLM insight text (ALWAYS when LLM is enabled, not just with custom MCPs)
    llm_text = ""
    if llm_enabled:
        # Build prompt with all available data
        prompt_data = {
            "city": primary_data.get("city", city),
            "weather": primary_data.get("weather", {}),
            "aqi": primary_data.get("aqi", {}),
            "current_time": primary_data.get("current_time", ""),
            "custom_sources": []
        }
        for custom in custom_mcp_results:
            prompt_data["custom_sources"].append({
                "server": custom["server_name"],
                "data": custom.get("data", {})
            })

        llm_prompt = f"""You are GeoBot, a friendly location intelligence assistant.
The user asked: "{user_input}"

Here is the real-time data for {primary_data.get('city', city)}:
{json.dumps(primary_data, indent=2)}"""

        if custom_mcp_results:
            llm_prompt += f"""

Additional data from custom MCP servers:
{json.dumps(custom_mcp_results, indent=2)}"""

        llm_prompt += """

Task: Provide a friendly, informative response about this location.
- Include interesting facts, travel tips, or cultural insights if relevant
- Reference the weather and air quality data naturally
- Keep it concise but engaging (2-4 sentences)
- Do NOT use bullet points, headers, or numbered lists
- Write in plain flowing paragraph text
- No emojis unless the user used them"""

        llm_text = generate_llm_text(llm_prompt)

    # Build custom_text only when there are custom MCP results (for backward compat)
    custom_text = ""
    if llm_enabled and custom_mcp_results:
        custom_text = llm_text

    # Merge all custom MCP flat data into primary_data for unified HUD rendering
    merged_hud = dict(primary_data)
    for custom in custom_mcp_results:
        for key, value in custom.get("data", {}).items():
            if key not in merged_hud and value is not None:
                merged_hud[key] = value

    return {
        "type": "hud_with_custom",
        "hud_data": merged_hud,
        "llm_text": llm_text,           # NEW: always include LLM text when enabled
        "custom_text": custom_text,    # Backward compat
        "custom_mcp_results": custom_mcp_results,
        "mcp_logs": all_logs
    }

@app.get("/health")
@app.head("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)