import os
import asyncio
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
    generate_llm_response,
    generate_llm_text
)

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
# 🏠 HOME
# ==============================
@app.get("/")
def home():
    return FileResponse("templates/index.html")


# ==============================
# 💬 CHAT API — STRUCTURED RESPONSE
# ==============================
@app.post("/chat")
async def chat(request: Request):
    try:
        body = await request.json()
        user_input = body.get("message", "").strip()

        if not user_input:
            return JSONResponse(
                status_code=400,
                content={"type": "error", "response": "❌ Empty message", "mcp_logs": []}
            )

        # run blocking agent in thread
        result = await asyncio.to_thread(process_query, user_input)
        return result

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"type": "error", "response": f"❌ Server error: {str(e)}", "mcp_logs": []}
        )


# ==============================
# 🧠 CORE LOGIC
# ==============================
def process_query(user_input: str):
    try:
        cities = extract_cities(user_input)
        all_logs = []

        # ======================
        # NO CITY → GENERAL LLM
        # ======================
        if not cities:
            response_text = generate_llm_text(user_input)
            return {
                "type": "text",
                "response": response_text,
                "mcp_logs": ["🤖 No city detected — routing to general LLM"]
            }

        # ======================
        # MULTI-CITY
        # ======================
        if len(cities) > 1:
            results = []

            for city in cities:
                result = call_mcp("getFullInsights", city)
                all_logs.extend(result.get("logs", []))

                if "error" in result:
                    continue

                results.append({city: clean_data(result["data"])})

            if not results:
                return {
                    "type": "error",
                    "response": "❌ Could not fetch data for any of the requested cities. Please check the city names and try again.",
                    "mcp_logs": all_logs
                }

            # For multi-city, return compare data
            compare_data = []
            for r in results:
                for city_name, city_data in r.items():
                    compare_data.append(city_data)

            return {
                "type": "compare",
                "response": compare_data,
                "mcp_logs": all_logs
            }

        # ======================
        # SINGLE CITY
        # ======================
        city = cities[0]
        tool = choose_tool(user_input)

        result = call_mcp(tool, city)
        all_logs.extend(result.get("logs", []))

        if "error" in result:
            return {
                "type": "error",
                "response": f"❌ {result['error']}",
                "mcp_logs": all_logs
            }

        cleaned = clean_data(result["data"])

        # Generate LLM-formatted response from MCP data
        prompt = build_prompt(user_input, cleaned)
        llm_text = generate_llm_response(prompt)

        # Return HUD-compatible data + LLM text
        return {
            "type": "hud",
            "response": cleaned,
            "llm_text": llm_text,
            "mcp_logs": all_logs
        }

    except Exception as e:
        return {
            "type": "error",
            "response": f"❌ Processing error: {str(e)}",
            "mcp_logs": all_logs if 'all_logs' in locals() else []
        }


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
