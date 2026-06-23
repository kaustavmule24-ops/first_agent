import os
import asyncio
import json
import logging
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# 🔥 import your existing agent functions
from agent import (
    extract_cities,
    choose_tool,
    call_mcp,
    clean_data,
    build_prompt,
    generate_llm_response
)

# ==============================
# 🪵 LOGGING CONFIG
# ==============================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("AGENT_APP")

app = FastAPI(title="MCP AI Agent 🌍")

# ==============================
# ✅ CORS (allow frontend calls)
# ==============================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================
# ♻️ KEEP-ALIVE (Self-ping to prevent Render sleep)
# ==============================

AGENT_URL = os.environ.get("AGENT_URL", "https://mcp-agent-1s2s.onrender.com")
KEEP_ALIVE_INTERVAL = 540  # 9 minutes (in seconds)

async def keep_alive_loop():
    """
    Background task that pings this server's /health every 9 minutes
    to prevent Render.com from spinning down the free tier instance.
    Zero LLM tokens used — only hits the health check endpoint.
    """
    logger.info(f"♻️ Keep-alive started — pinging {AGENT_URL}/health every {KEEP_ALIVE_INTERVAL // 60} minutes")
    
    # Wait a bit on first startup so the server is fully ready
    await asyncio.sleep(30)
    
    while True:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as ping_client:
                response = await ping_client.get(
                    f"{AGENT_URL}/health",
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    logger.info("♻️ Keep-alive ping successful — agent is awake")
                else:
                    logger.warning(f"♻️ Keep-alive ping returned status {response.status_code}")
                    
        except Exception as e:
            logger.warning(f"♻️ Keep-alive ping failed: {e}")
        
        await asyncio.sleep(KEEP_ALIVE_INTERVAL)


@app.on_event("startup")
async def startup_event():
    """
    Start the keep-alive background task when the server boots up.
    """
    asyncio.create_task(keep_alive_loop())
    logger.info("🚀 Agent App startup complete — keep-alive task registered")


# ==============================
# 🏠 HOME (NO JINJA → NO ERROR)
# ==============================
@app.get("/")
def home():
    return FileResponse("templates/index.html")


# ==============================
# 💬 CHAT API — MAIN ENTRY POINT
# ==============================

@app.post("/chat")
async def chat(request: Request):
    try:
        body = await request.json()
        user_input = body.get("message", "").strip()

        if not user_input:
            return {"response": "❌ Empty message", "type": "text"}

        # run blocking agent in thread
        result = await asyncio.to_thread(process_query, user_input)

        return result  # Returns dict with response, type, mcp_logs

    except Exception as e:
        logger.exception(f"💥 CHAT ERROR: {e}")
        return JSONResponse(
            status_code=500,
            content={"response": f"❌ {str(e)}", "type": "error"}
        )


# ==============================
# 🧠 CORE LOGIC — ROUTES TO LLM OR MCP
# ==============================

def process_query(user_input: str) -> dict:
    """
    Main routing logic:
    - No city found → LLM (general question)
    - City found → MCP (weather data) → optional LLM formatting
    - MCP fails → Retry once → Error (NO LLM for retry)
    """
    try:
        cities = extract_cities(user_input)
        logger.info(f"🔍 Cities detected: {cities}")

        # ======================
        # NO CITY → GENERAL QUESTION → LLM
        # ======================
        if not cities:
            logger.info("🤖 No city found → Routing to LLM")
            llm_response = generate_llm_response(
                f"You are GeoBot, a helpful location intelligence assistant. "
                f"Answer the user's question naturally and concisely.\n\n"
                f"User: {user_input}"
            )
            return {
                "response": llm_response,
                "type": "text",
                "mcp_logs": []
            }

        # ======================
        # MULTI-CITY → MCP FOR EACH
        # ======================
        if len(cities) > 1:
            logger.info(f"🌍 Multi-city mode: {cities}")
            return process_multi_city(user_input, cities)

        # ======================
        # SINGLE CITY → MCP → DATA
        # ======================
        city = cities[0]
        tool = choose_tool(user_input)
        logger.info(f"🌍 Single city: {city} | Tool: {tool}")

        return process_single_city(user_input, city, tool)

    except Exception as e:
        logger.exception(f"💥 PROCESSING ERROR: {e}")
        return {
            "response": f"❌ Processing error: {str(e)}",
            "type": "error",
            "mcp_logs": []
        }


def process_single_city(user_input: str, city: str, tool: str) -> dict:
    """
    Single city flow:
    1. Call MCP with selected tool
    2. If fails, retry with getFullInsights
    3. If still fails, return error (NO LLM)
    4. If success, return structured data for HUD
    """
    mcp_logs = []

    # --- Attempt 1: Selected tool ---
    log1 = f"📡 Calling MCP: {tool} → {city}"
    logger.info(log1)
    mcp_logs.append(log1)

    data = call_mcp(tool, city)

    # Check if failed
    if isinstance(data, dict) and "error" in data:
        log_err = f"❌ MCP failed: {data['error']}"
        logger.warning(log_err)
        mcp_logs.append(log_err)

        # --- Attempt 2: Retry with getFullInsights ---
        log_retry = "🔄 Retrying with getFullInsights..."
        logger.info(log_retry)
        mcp_logs.append(log_retry)

        data = call_mcp("getFullInsights", city)

        if isinstance(data, dict) and "error" in data:
            log_fail = f"❌ Retry also failed: {data['error']}"
