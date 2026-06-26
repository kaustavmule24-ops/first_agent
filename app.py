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
    # CHECK IF ANY MCP IS ENABLED
    # ======================
    enabled_servers = [s for s in mcp_servers if s.get("enabled", False)]
    
    if not enabled_servers:
        # No MCP connected — tell user to connect one
        return {
            "type": "need_mcp",
            "response": "🔌 No MCP server connected.<br><br>To get weather, AQI, and location data, please connect an MCP server first:<br><br>1. Click ⚙️ Settings (top-left)<br>2. Go to '🔗 MCP Servers'<br>3. Click '➕ Add New Server' and enter your MCP URL<br>4. Enable the server using the toggle<br><br>You can use any MCP server that supports weather/location data.",
            "mcp_logs": ["⚠️ No MCP servers enabled — user needs to connect one"]
        }

    # ======================
    # MULTI-CITY: Compare mode
    # ======================
    if len(cities) > 1:
        results = []
        for c in cities:
            # Use the first enabled server for now (or could parallel call all)
            server = enabled_servers[0]
            r = call_mcp("getFullInsights", c, custom_url=server["url"], server_config=server.get("config"))
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

    # Call ALL enabled MCP servers
    all_mcp_results = []
    for server in enabled_servers:
        result = call_mcp(
            tool,
            city,
            custom_url=server["url"],
            server_config=server.get("config")
        )
        all_logs.extend(result.get("logs", []))
        if "error" not in result:
            all_mcp_results.append({
                "server_name": server["name"],
                "data": result["data"],
                "format": result.get("format", "unknown")
            })

    if not all_mcp_results:
        return {
            "type": "error",
            "response": f"❌ All connected MCP servers failed to return data for {city}.",
            "mcp_logs": all_logs
        }

    # Use first result as primary HUD data
    primary_data = clean_data(all_mcp_results[0]["data"])

    # Build custom MCP results (all servers beyond the first)
    custom_mcp_results = []
    for i, custom in enumerate(all_mcp_results):
        raw_data = custom["data"]
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
            "server_name": custom["server_name"],
            "data": flattened,
            "format": custom.get("format", "unknown")
        })

    # Build custom text via LLM
    custom_text = ""
    if llm_enabled and custom_mcp_results:
        custom_prompt = f"""You are GeoBot, a location intelligence assistant.

The user asked: "{user_input}"

Data for {primary_data.get('city', 'this city')}:
{json.dumps(primary_data, indent=2)}

External MCP data:
{json.dumps(custom_mcp_results, indent=2)}

Task: Answer the user's question directly.
- If the External MCP data is relevant to "{user_input}", use it as the primary source.
- If not relevant, answer based on the available data or your own general knowledge.
- Do NOT use bullet points, headers, or numbered lists. Write in plain flowing text. Max 100 words. No emojis unless the user used them."""
        custom_text = generate_llm_text(custom_prompt)

    # Merge all custom MCP flat data into primary_data for unified HUD rendering
    merged_hud = dict(primary_data)
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
@app.head("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)