import os
import asyncio
import json
import aiohttp
from typing import Optional
from fastapi import FastAPI, Request, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt import PyJWKClient

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
# CLERK AUTH CONFIG
# ==============================
CLERK_JWKS_URL = "https://excited-ibex-65.clerk.accounts.dev/.well-known/jwks.json"
CLERK_ISSUER = "https://excited-ibex-65.clerk.accounts.dev"

jwks_client = PyJWKClient(CLERK_JWKS_URL)
security = HTTPBearer(auto_error=False)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify Clerk JWT and return user info. Returns None if no token or invalid.
    Also checks if email is verified — rejects unverified users."""
    if not credentials:
        return None
    try:
        signing_key = jwks_client.get_signing_key_from_jwt(credentials.credentials)
        payload = jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["RS256"],
            issuer=CLERK_ISSUER,
            options={"verify_aud": False, "verify_exp": True}
        )

        # Check if email is verified
        email_verified = payload.get("email_verified", False)
        if not email_verified:
            return {"_unverified": True, "email": payload.get("email")}

        return {
            "user_id": payload.get("sub"),
            "email": payload.get("email"),
            "name": payload.get("name") or payload.get("email"),
            "email_verified": True
        }
    except Exception:
        return None


# ==============================
# SELF-PING TO PREVENT SLEEP
# ==============================
async def self_ping():
    """Ping own health endpoint every 10 minutes to prevent Render free tier sleep."""
    await asyncio.sleep(30)
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
        await asyncio.sleep(600)


@app.on_event("startup")
async def startup_event():
    """Start background tasks on server startup."""
    asyncio.create_task(self_ping())


@app.get("/")
def home():
    return FileResponse("templates/index.html")


# ==============================
# AUTH ENDPOINTS
# ==============================
@app.get("/api/user")
async def api_user(user: Optional[dict] = Depends(get_current_user)):
    """Return current authenticated user info."""
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user": user}


# ==============================
# CHAT ENDPOINT
# ==============================
@app.post("/chat")
async def chat(request: Request, user: Optional[dict] = Depends(get_current_user)):
    # Mandatory authentication — reject if not logged in
    if not user:
        return JSONResponse(
            status_code=401,
            content={"type": "error", "response": "🔐 Authentication required. Please sign in to use GeoBot."}
        )

    # Reject unverified email users
    if user.get("_unverified"):
        return JSONResponse(
            status_code=403,
            content={
                "type": "error", 
                "response": "📧 Email verification required.<br><br>Please check your inbox and verify your email address before using GeoBot.<br><br>Didn't receive it? Check your spam folder or try signing in again."
            }
        )

    try:
        body = await request.json()
        user_input = body.get("message", "").strip()
        llm_enabled = body.get("llm_enabled", True)
        mcp_enabled = body.get("mcp_enabled", False)
        mcp_servers = body.get("mcp_servers", [])

        if not user_input:
            return JSONResponse(
                status_code=400,
                content={"type": "error", "response": "❌ Empty message", "mcp_logs": []}
            )

        result = await asyncio.to_thread(
            process_query, user_input, llm_enabled, mcp_servers, mcp_enabled, user
        )
        return result

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"type": "error", "response": f"❌ Server error: {str(e)}", "mcp_logs": []}
        )


def process_query(user_input: str, llm_enabled: bool, mcp_servers=None, mcp_master_enabled=False, user=None):
    all_logs = []
    cities = extract_cities(user_input)
    mcp_servers = mcp_servers or []

    # Log user if authenticated (no DB, just for server logs)
    if user:
        all_logs.append(f"👤 Authenticated user: {user.get('email', 'unknown')}")

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
            default_servers = [s for s in enabled_servers if s.get("isDefault") == True]
            custom_servers = [s for s in enabled_servers if s.get("isDefault") != True]

            city_data = None
            if default_servers:
                r = call_mcp("getFullInsights", c, custom_url=default_servers[0]["url"], server_config=default_servers[0].get("config"))
                all_logs.extend(r.get("logs", []))
                if "error" not in r:
                    city_data = clean_data(r["data"])

            if city_data is None and custom_servers:
                for server in custom_servers:
                    r = call_mcp("getFullInsights", c, custom_url=server["url"], server_config=server.get("config"))
                    all_logs.extend(r.get("logs", []))
                    if "error" not in r:
                        city_data = clean_data(r["data"])
                        break

            if city_data:
                results.append(city_data)

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
    # SINGLE CITY: Call default + custom MCPs separately
    # ======================
    city = cities[0]
    tool = choose_tool(user_input)

    default_servers = [s for s in enabled_servers if s.get("isDefault") == True]
    custom_servers = [s for s in enabled_servers if s.get("isDefault") != True]

    default_data = None
    if default_servers:
        default_result = call_mcp(tool, city, custom_url=default_servers[0]["url"], server_config=default_servers[0].get("config"))
        all_logs.extend(default_result.get("logs", []))
        if "error" not in default_result:
            default_data = clean_data(default_result["data"])

    custom_mcp_results = []
    for server in custom_servers:
        result = call_mcp(
            tool,
            city,
            custom_url=server["url"],
            server_config=server.get("config")
        )
        all_logs.extend(result.get("logs", []))
        if "error" not in result:
            raw_data = result["data"]
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
        if custom_mcp_results:
            first_custom = custom_mcp_results[0]["data"]
            default_data = {
                "city": city,
                "country": first_custom.get("country", "Unknown"),
                "current_time": first_custom.get("current_time", ""),
                "weather": {
                    "temperature": first_custom.get("temperature", 0),
                    "weathercode": first_custom.get("weathercode", 0),
                    "is_day": first_custom.get("is_day", 1)
                },
                "aqi": {
                    "us_aqi": first_custom.get("us_aqi", 0) or first_custom.get("aqi", 0)
                }
            }
            for custom in custom_mcp_results:
                for key, value in custom["data"].items():
                    if key not in default_data and value is not None:
                        default_data[key] = value
        else:
            return {
                "type": "error",
                "response": f"❌ No MCP server returned data for {city}.",
                "mcp_logs": all_logs
            }

    primary_data = dict(default_data)
    for custom in custom_mcp_results:
        for key, value in custom.get("data", {}).items():
            if key not in primary_data and value is not None:
                primary_data[key] = value

    custom_text = ""
    if llm_enabled:
        if custom_mcp_results:
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
        else:
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