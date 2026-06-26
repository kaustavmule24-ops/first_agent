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
    call_mcp_multi,
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
        return {
            "type": "need_mcp",
            "response": "🔌 MCP is disabled.<br><br>To get weather, AQI, and location data, please enable MCP:<br><br>1. Click ⚙️ Settings (top-left)<br>2. Toggle ON the 🌐 MCP Server switch<br><br>Then enable at least one MCP server from the list.",
            "mcp_logs": ["⚠️ Master MCP toggle is OFF"]
        }

    # ======================
    # CHECK IF ANY MCP SERVER IS ENABLED
    # ======================
    enabled_servers = [s for s in mcp_servers if s.get("enabled") == True]
    if not enabled_servers:
        return {
            "type": "need_mcp",
            "response": "🔌 No MCP servers are enabled.<br><br>To get weather, AQI, and location data, please enable at least one MCP server:<br><br>1. Click ⚙️ Settings (top-left)<br>2. Find your MCP server in the list<br>3. Toggle it ON",
            "mcp_logs": ["⚠️ No MCP servers enabled"]
        }

    # ======================
    # MULTI-CITY: Compare mode
    # ======================
    if len(cities) > 1:
        results = []
        for c in cities:
            merged_result = call_mcp_multi("getFullInsights", c, enabled_servers)
            all_logs.extend(merged_result.get("logs", []))
            if "error" not in merged_result:
                results.append(clean_data(merged_result["data"]))

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
    # SINGLE CITY: Call ONLY enabled MCPs from frontend
    # ======================
    city = cities[0]
    tool = choose_tool(user_input)

    merged_result = call_mcp_multi(tool, city, enabled_servers)
    all_logs.extend(merged_result.get("logs", []))

    if "error" in merged_result:
        return {
            "type": "error",
            "response": f"❌ MCP failed: {merged_result['error']}",
            "mcp_logs": all_logs
        }

    primary_data = clean_data(merged_result["data"])

    # Build LLM text
    custom_text = ""
    if llm_enabled:
        custom_prompt = f"""You are GeoBot, a location intelligence assistant.

The user asked: "{user_input}"

Data for {primary_data.get('city', 'this city')}:
{json.dumps(primary_data, indent=2)}

Task: Answer the user's question directly based on the weather and location data above.
- Describe the current conditions in plain, natural language.
- Mention temperature, weather condition, and any notable metrics (AQI, humidity, wind, etc.).
- Do NOT use bullet points, headers, or numbered lists. Write in plain flowing text. Max 100 words. No emojis unless the user used them."""
        
        custom_text = generate_llm_text(custom_prompt)

    return {
        "type": "hud_with_custom",
        "hud_data": primary_data,
        "custom_text": custom_text,
        "custom_mcp_results": [],
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