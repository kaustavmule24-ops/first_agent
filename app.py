import os
import asyncio
import json
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

@app.get("/")
def home():
    return FileResponse("templates/index.html")


@app.post("/chat")
async def chat(request: Request):
    try:
        body = await request.json()
        user_input = body.get("message", "").strip()
        llm_enabled = body.get("llm_enabled", True)

        if not user_input:
            return JSONResponse(
                status_code=400,
                content={"type": "error", "response": "❌ Empty message", "mcp_logs": []}
            )

        result = await asyncio.to_thread(process_query, user_input, llm_enabled)
        return result

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"type": "error", "response": f"❌ Server error: {str(e)}", "mcp_logs": []}
        )


def process_query(user_input: str, llm_enabled: bool):
    all_logs = []
    cities = extract_cities(user_input)

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
    # MULTI-CITY
    # ======================
    if len(cities) > 1:
        results = []
        for city in cities:
            result = call_mcp("getFullInsights", city)
            all_logs.extend(result.get("logs", []))
            if "error" not in result:
                results.append(clean_data(result["data"]))

        if not results:
            return {
                "type": "error",
                "response": "❌ Could not fetch data for any of the requested cities. Please check the city names and try again.",
                "mcp_logs": all_logs
            }

        # Generate LLM comparison summary
        llm_text = ""
        if llm_enabled:
            compare_prompt = f"""Compare these cities based on the data:
{json.dumps(results, indent=2)}

Provide a brief comparison (2-3 sentences) highlighting key differences in weather, air quality, and any interesting observations."""
            llm_text = generate_llm_text(compare_prompt)

        return {
            "type": "compare",
            "response": results,
            "llm_text": llm_text,
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

    llm_text = ""
    if llm_enabled:
        llm_text = generate_city_insights(user_input, cleaned)

    return {
        "type": "hud",
        "response": cleaned,
        "llm_text": llm_text if llm_enabled else "",
        "mcp_logs": all_logs
    }


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
