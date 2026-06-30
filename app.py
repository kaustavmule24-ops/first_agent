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
    generate_llm_text,
    llm_decide_needs_mcp,
    llm_generate_with_data,
    llm_generate_general
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
    """Verify Clerk JWT and return user info. Returns None if no token or invalid."""
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

        return {
            "user_id": payload.get("sub"),
            "email": payload.get("email"),
            "name": payload.get("name") or payload.get("email"),
            "token": credentials.credentials
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



    try:
        body = await request.json()
        user_input = body.get("message", "").strip()
        llm_enabled = body.get("llm_enabled", True)
        mcp_enabled = body.get("mcp_enabled", False)
        mcp_servers = body.get("mcp_servers", [])
        
        # Inject Weather MCP connection state from frontend into server objects
        for s in mcp_servers:
            if s.get("isDefault") == True:
                s["connected"] = s.get("connected", s.get("enabled", False))

        if not user_input:
            return JSONResponse(
                status_code=400,
                content={"type": "error", "response": "❌ Empty message", "mcp_logs": []}
            )

        clerk_token = user.get("token") if user else None
        result = await asyncio.to_thread(
            process_query, user_input, llm_enabled, mcp_servers, mcp_enabled, user, clerk_token
        )
        return result

    except Exception as e:
        import traceback
        print(f"❌ CHAT ERROR: {str(e)}")
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={"type": "error", "response": f"❌ Server error: {str(e)}", "mcp_logs": []}
        )


def process_query(user_input: str, llm_enabled: bool, mcp_servers=None, mcp_master_enabled=False, user=None, clerk_token=None):
    """
    NEW ORCHESTRATOR: LLM-first decision making.
    1. Ask LLM what the user needs
    2. If general chat → LLM answers directly
    3. If city data needed → Check Weather MCP connection → Call MCP → LLM synthesizes response
    """
    # Safety check: ensure imports are available
    try:
        llm_decide_needs_mcp
        llm_generate_with_data
        llm_generate_general
    except NameError as e:
        return {
            "type": "error",
            "response": f"❌ Server config error: Missing import {e}. Please restart the server.",
            "mcp_logs": []
        }
    
    all_logs = []
    mcp_servers = mcp_servers or []

    # Log user if authenticated
    if user:
        all_logs.append(f"👤 Authenticated user: {user.get('email', 'unknown')}")

    # ======================
    # STEP 1: LLM decides what user needs
    # ======================
    if not llm_enabled:
        # LLM disabled — use old fallback logic
        return process_query_fallback(user_input, mcp_servers, mcp_master_enabled, all_logs)

    decision = llm_decide_needs_mcp(user_input)
    all_logs.append(f"🧠 LLM decision: needs_mcp={decision['needs_mcp']}, cities={decision['cities']}, reasoning={decision['reasoning']}")

    # ======================
    # STEP 2: General chat (no MCP needed)
    # ======================
    if not decision["needs_mcp"] or decision["is_general_chat"]:
        response_text = llm_generate_general(user_input)
        return {
            "type": "text",
            "response": response_text,
            "mcp_logs": all_logs
        }

    # ======================
    # STEP 3: MCP needed — check if Weather MCP is connected
    # ======================
    cities = decision["cities"]
    if not cities:
        all_logs.append("⚠️ LLM said MCP needed but no cities found")
        response_text = llm_generate_general(user_input)
        return {
            "type": "text",
            "response": response_text,
            "mcp_logs": all_logs
        }

    # Check Weather MCP connection state from frontend payload
    weather_mcp_connected = False
    weather_server = None
    for s in mcp_servers:
        if s.get("isDefault") == True:
            weather_server = s
            # Frontend sends connected state via the server's enabled flag
            # OR we check if it's in the enabled list
            weather_mcp_connected = s.get("enabled") == True and s.get("connected") != False
            break

    # If Weather MCP is not connected but we need city data
    if not weather_mcp_connected and not mcp_master_enabled:
        return {
            "type": "need_mcp",
            "response": "🔌 MCP is disabled.<br><br>To get weather, AQI, and location data, please enable MCP:<br><br>1. Click ⚙️ Settings (top-left)<br>2. Toggle ON the 🌐 MCP Server switch<br>3. Click 🔗 Connect on the Weather MCP card",
            "mcp_logs": all_logs
        }

    if not weather_mcp_connected:
        # Weather MCP disconnected but needed — return LLM-only + connect prompt
        llm_response = llm_generate_general(user_input)
        full_response = f"{llm_response}\n\n---\n\n💡 **Want live data?** Connect the Weather MCP in Settings for real-time weather, AQI, and time data."
        return {
            "type": "need_connect_weather",
            "response": full_response,
            "mcp_logs": all_logs
        }

    # ======================
    # STEP 4: Weather MCP is connected — proceed with MCP calls
    # ======================
    enabled_servers = [s for s in mcp_servers if s.get("enabled") == True]
    if not enabled_servers:
        return {
            "type": "need_mcp",
            "response": "🔌 No MCP servers are enabled.<br><br>To get weather, AQI, and location data, please enable at least one MCP server:<br><br>1. Click ⚙️ Settings (top-left)<br>2. Find your MCP server in the list<br>3. Toggle it ON",
            "mcp_logs": all_logs
        }

    # ======================
    # MULTI-CITY: Compare mode
    # ======================
    if decision["is_compare"] or len(cities) > 1:
        results = []
        for c in cities:
            default_servers = [s for s in enabled_servers if s.get("isDefault") == True]
            custom_servers = [s for s in enabled_servers if s.get("isDefault") != True]

            city_data = None
            if default_servers:
                r = call_mcp("getFullInsights", c, custom_url=default_servers[0]["url"], server_config=default_servers[0].get("config"), auth_token=clerk_token)
                all_logs.extend(r.get("logs", []))
                if "error" not in r:
                    city_data = clean_data(r["data"])

            if city_data is None and custom_servers:
                for server in custom_servers:
                    r = call_mcp("getFullInsights", c, custom_url=server["url"], server_config=server.get("config"), auth_token=clerk_token)
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

        # LLM generates comparison text
        llm_text = llm_generate_with_data(user_input, results, decision["reasoning"])

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
    tool = decision["tools"][0] if decision["tools"] else "getFullInsights"

    default_servers = [s for s in enabled_servers if s.get("isDefault") == True]
    custom_servers = [s for s in enabled_servers if s.get("isDefault") != True]

    default_data = None
    if default_servers:
        default_result = call_mcp(tool, city, custom_url=default_servers[0]["url"], server_config=default_servers[0].get("config"), auth_token=clerk_token)
        all_logs.extend(default_result.get("logs", []))
        if "error" not in default_result:
            default_data = clean_data(default_result["data"])

    custom_mcp_results = []
    for server in custom_servers:
        result = call_mcp(
            tool,
            city,
            custom_url=server["url"],
            server_config=server.get("config"),
            auth_token=clerk_token
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

    # LLM generates final response with data
    custom_text = llm_generate_with_data(user_input, primary_data, decision["reasoning"])

    return {
        "type": "hud_with_custom",
        "hud_data": primary_data,
        "custom_text": custom_text,
        "custom_mcp_results": custom_mcp_results,
        "mcp_logs": all_logs
    }


def process_query_fallback(user_input: str, mcp_servers=None, mcp_master_enabled=False, all_logs=None, clerk_token=None):
    """
    Fallback when LLM is disabled. Uses old direct logic.
    """
    all_logs = all_logs or []
    cities = extract_cities(user_input)
    mcp_servers = mcp_servers or []

    if not cities:
        return {
            "type": "need_llm",
            "response": "I need a city name to fetch weather data. Please mention a city (e.g., 'Weather in Delhi').\n\nOr enable 🤖 LLM in Settings for AI-powered answers to general questions.",
            "mcp_logs": all_logs
        }

    if not mcp_master_enabled:
        return {
            "type": "need_mcp",
            "response": "🔌 MCP is disabled.<br><br>To get weather, AQI, and location data, please enable MCP:<br><br>1. Click ⚙️ Settings (top-left)<br>2. Toggle ON the 🌐 MCP Server switch<br><br>Then enable at least one MCP server from the list.",
            "mcp_logs": all_logs
        }

    enabled_servers = [s for s in mcp_servers if s.get("enabled") == True]
    if not enabled_servers:
        return {
            "type": "need_mcp",
            "response": "🔌 No MCP servers are enabled.<br><br>To get weather, AQI, and location data, please enable at least one MCP server:<br><br>1. Click ⚙️ Settings (top-left)<br>2. Find your MCP server in the list<br>3. Toggle it ON",
            "mcp_logs": all_logs
        }

    # Single city fallback
    city = cities[0]
    tool = choose_tool(user_input)

    default_servers = [s for s in enabled_servers if s.get("isDefault") == True]
    custom_servers = [s for s in enabled_servers if s.get("isDefault") != True]

    default_data = None
    if default_servers:
        default_result = call_mcp(tool, city, custom_url=default_servers[0]["url"], server_config=default_servers[0].get("config"), auth_token=clerk_token)
        all_logs.extend(default_result.get("logs", []))
        if "error" not in default_result:
            default_data = clean_data(default_result["data"])

    if default_data is None:
        return {
            "type": "error",
            "response": f"❌ No MCP server returned data for {city}.",
            "mcp_logs": all_logs
        }

    return {
        "type": "hud",
        "response": default_data,
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