import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import logging

# 🔥 import your existing agent functions
from agent import (
    extract_cities,
    choose_tool,
    call_mcp,
    clean_data,
    build_prompt,
    generate_llm_response
)

app = FastAPI(title="MCP AI Agent 🌍")

# ==============================
# 🪵 LOGGING
# ==============================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("AGENT_APP")

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

AGENT_URL = os.environ.get("AGENT_URL", "https://first-agent-6wlz.onrender.com")
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
# 💬 CHAT API
# ==============================
@app.post("/chat")
async def chat(request: Request):
    try:
        body = await request.json()
        user_input = body.get("message", "").strip()

        if not user_input:
            return {"response": "❌ Empty message"}

        # run blocking agent in thread
        result = await asyncio.to_thread(process_query, user_input)

        return {"response": result}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"response": f"❌ {str(e)}"}
        )


# ==============================
# 🧠 CORE LOGIC (UNCHANGED)
# ==============================
def process_query(user_input: str) -> str:
    try:
        cities = extract_cities(user_input)

        if not cities:
            return "❌ No city found"

        # ======================
        # MULTI-CITY
        # ======================
        if len(cities) > 1:
            results = []

            for city in cities:
                data = call_mcp("getFullInsights", city)

                if isinstance(data, dict) and "error" in data:
                    continue

                results.append({city: clean_data(data)})

            if not results:
                return "❌ No valid data found"

            prompt = build_prompt(user_input, results)

        # ======================
        # SINGLE CITY
        # ======================
        else:
            city = cities[0]
            tool = choose_tool(user_input)

            data = call_mcp(tool, city)

            if isinstance(data, dict) and "error" in data:
                return data["error"]

            prompt = build_prompt(user_input, clean_data(data))

        # ======================
        # LLM RESPONSE
        # ======================
        return generate_llm_response(prompt)

    except Exception as e:
        return f"❌ Processing error: {str(e)}"


# ==============================
# ❤️ HEALTH CHECK
# ==============================
@app.get("/health")
def health():
    return {"status": "ok"}


# ==============================
# ▶️ RUN SERVER
# ==============================
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        reload=True
    )
