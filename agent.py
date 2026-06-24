import requests
import json
import re
import os
import time
from groq import Groq

# ==============================
# 🎨 COLORS
# ==============================
class Color:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    END = "\033[0m"


# ==============================
# 🔗 CONFIG
# ==============================
DEFAULT_MCP_URL = "https://mcp-weather-s1s0.onrender.com/tool"
MCP_URL = DEFAULT_MCP_URL

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

if not GROQ_API_KEY:
    print("❌ Set GROQ_API_KEY environment variable first")
    raise SystemExit(1)

client = Groq(api_key=GROQ_API_KEY)
MODEL = "llama-3.1-8b-instant"


# ==============================
# 🔍 PROTOCOL ENUMS & CACHE
# ==============================

class MCPFormat:
    CUSTOM = "custom"
    JSONRPC = "jsonrpc"
    REST_API = "rest_api"
    UNKNOWN = "unknown"

FORMAT_CACHE = {}
TOOLS_CACHE = {}


# ==============================
# 🌐 PROTOCOL DETECTION ENGINE
# ==============================

def detect_mcp_format(url, timeout=10):
    logs = []

    if url in FORMAT_CACHE:
        logs.append(f"📋 Using cached format for {url}: {FORMAT_CACHE[url]}")
        return FORMAT_CACHE[url], TOOLS_CACHE.get(url, []), logs

    logs.append(f"🔍 Detecting MCP format for: {url}")
    print(f"{Color.YELLOW}🔍 Detecting MCP format for: {url}{Color.END}")

    detected_format = MCPFormat.UNKNOWN
    available_tools = []

    # Strategy 1: CUSTOM format probe
    try:
        custom_payload = {"tool": "healthCheck", "input": "test"}
        res = requests.post(url, json=custom_payload, timeout=timeout)

        if res.status_code == 200:
            data = res.json()
            if "status" in data and "server" in data:
                detected_format = MCPFormat.CUSTOM
                logs.append("✅ Detected: CUSTOM format (GeoBot protocol)")
                print(f"{Color.GREEN}✅ Detected: CUSTOM format{Color.END}")
                if "features" in data:
                    available_tools = list(data["features"].keys())
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs
            if "jsonrpc" in data:
                detected_format = MCPFormat.JSONRPC
                logs.append("✅ Detected: JSON-RPC format (from healthCheck response)")
                print(f"{Color.GREEN}✅ Detected: JSON-RPC format{Color.END}")
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs

    except Exception as e:
        logs.append(f"⚠️ CUSTOM probe failed: {str(e)}")

    # Strategy 2: JSON-RPC format probe
    try:
        jsonrpc_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {}
        }
        res = requests.post(url, json=jsonrpc_payload, timeout=timeout)

        if res.status_code == 200:
            data = res.json()
            if "jsonrpc" in data and ("result" in data or "error" in data):
                detected_format = MCPFormat.JSONRPC
                logs.append("✅ Detected: JSON-RPC format (Anthropic MCP)")
                print(f"{Color.GREEN}✅ Detected: JSON-RPC format{Color.END}")
                if "result" in data and "tools" in data["result"]:
                    available_tools = [t.get("name") for t in data["result"]["tools"] if t.get("name")]
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs
            if "tools" in data:
                detected_format = MCPFormat.JSONRPC
                logs.append("✅ Detected: JSON-RPC format (plain tools response)")
                print(f"{Color.GREEN}✅ Detected: JSON-RPC format{Color.END}")
                available_tools = [t.get("name") for t in data["tools"] if t.get("name")]
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs

    except Exception as e:
        logs.append(f"⚠️ JSON-RPC probe failed: {str(e)}")

    # Strategy 3: REST API probe
    try:
        base_url = url.replace('/tool', '').replace('/mcp', '')
        res = requests.get(base_url, timeout=timeout)
        if res.status_code in [200, 401, 403]:
            content_type = res.headers.get('Content-Type', '')
            if 'json' in content_type or 'application/json' in content_type:
                detected_format = MCPFormat.REST_API
                logs.append(f"✅ Detected: REST API format (GET returned JSON, status {res.status_code})")
                print(f"{Color.GREEN}✅ Detected: REST API format{Color.END}")
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs
            text = res.text.lower()
            if any(x in text for x in ['api', 'documentation', 'endpoints', 'swagger', 'openapi']):
                detected_format = MCPFormat.REST_API
                logs.append("✅ Detected: REST API format (documentation page)")
                print(f"{Color.GREEN}✅ Detected: REST API format{Color.END}")
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs

    except Exception as e:
        logs.append(f"⚠️ REST API probe failed: {str(e)}")

    # Strategy 4: Error response probe
    try:
        res = requests.post(url, timeout=timeout)
        if res.status_code in [400, 401, 403, 404, 405]:
            error_text = res.text.lower()
            if 'jsonrpc' in error_text or 'method' in error_text:
                detected_format = MCPFormat.JSONRPC
                logs.append("✅ Detected: JSON-RPC format (from error response)")
                print(f"{Color.GREEN}✅ Detected: JSON-RPC format (from error){Color.END}")
            elif 'tool' in error_text or 'input' in error_text:
                detected_format = MCPFormat.CUSTOM
                logs.append("✅ Detected: CUSTOM format (from error response)")
                print(f"{Color.GREEN}✅ Detected: CUSTOM format (from error){Color.END}")
            else:
                detected_format = MCPFormat.REST_API
                logs.append("✅ Detected: REST API format (from error response)")
                print(f"{Color.GREEN}✅ Detected: REST API format (from error){Color.END}")
            FORMAT_CACHE[url] = detected_format
            TOOLS_CACHE[url] = available_tools
            return detected_format, available_tools, logs

    except Exception as e:
        logs.append(f"⚠️ Error probe failed: {str(e)}")

    # Fallback
    logs.append("⚠️ Could not auto-detect format. Defaulting to CUSTOM.")
    print(f"{Color.YELLOW}⚠️ Could not auto-detect format. Defaulting to CUSTOM.{Color.END}")
    detected_format = MCPFormat.CUSTOM
    FORMAT_CACHE[url] = detected_format
    TOOLS_CACHE[url] = available_tools
    return detected_format, available_tools, logs


def build_mcp_payload(tool, city, mcp_format, available_tools=None, server_config=None):
    tool_mapping = {
        "getFullInsights": "getFullInsights",
        "getWeatherOnly": "getWeatherOnly",
        "getAQI": "getAQI",
        "getTimeOnly": "getTimeOnly",
        "getCoordinatesOnly": "getCoordinatesOnly",
        "getTodaySpecial": "getTodaySpecial",
        "healthCheck": "healthCheck"
    }

    if available_tools:
        tool_lower = tool.lower()
        for server_tool in available_tools:
            if tool_lower in server_tool.lower() or server_tool.lower() in tool_lower:
                tool_mapping[tool] = server_tool
                break

    actual_tool = tool_mapping.get(tool, tool)
    config = server_config or {}

    if mcp_format == MCPFormat.JSONRPC:
        return {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "tools/call",
            "params": {
                "name": actual_tool,
                "arguments": {
                    "city": city,
                    "location": city,
                    "input": city
                }
            }
        }

    elif mcp_format == MCPFormat.REST_API:
        return {
            "_format": "rest_api",
            "_tool": actual_tool,
            "_city": city,
            "_config": config
        }

    else:
        return {
            "tool": actual_tool,
            "input": city
        }


def call_mcp_rest_api(url, tool, city, server_config, timeout=15):
    logs = []
    config = server_config or {}

    try:
        endpoint = config.get('endpoint_template', '/tool')
        endpoint = endpoint.replace('{city}', city)
        endpoint = endpoint.replace('{tool}', tool)

        full_url = url.replace('/tool', '').rstrip('/')
        if not endpoint.startswith('/'):
            endpoint = '/' + endpoint
        full_url = full_url + endpoint

        logs.append(f"🌐 REST API call: {config.get('method', 'GET')} {full_url}")
        print(f"{Color.CYAN}🌐 REST API: {full_url}{Color.END}")

        headers = {'Content-Type': 'application/json'}
        auth_type = config.get('auth_type', 'none')
        auth_key = config.get('auth_key', '')
        auth_value = config.get('auth_value', '')

        if auth_type == 'header' and auth_key and auth_value:
            headers[auth_key] = auth_value
        elif auth_type == 'bearer' and auth_value:
            headers['Authorization'] = f'Bearer {auth_value}'

        params = {}
        if auth_type == 'query_param' and auth_key and auth_value:
            params[auth_key] = auth_value

        extra_params = config.get('params', {})
        if isinstance(extra_params, dict):
            params.update(extra_params)

        method = config.get('method', 'GET').upper()

        if method == 'POST':
            body = config.get('body_template', {})
            if isinstance(body, dict):
                body_str = json.dumps(body)
                body_str = body_str.replace('{city}', city).replace('{tool}', tool)
                body = json.loads(body_str)
            res = requests.post(full_url, headers=headers, params=params, json=body, timeout=timeout)
        else:
            res = requests.get(full_url, headers=headers, params=params, timeout=timeout)

        logs.append(f"⬅️ Status: {res.status_code}")

        if res.status_code != 200:
            return {"error": f"HTTP {res.status_code}: {res.text[:200]}", "logs": logs}

        raw_data = res.json()

        mapping = config.get('response_mapping', {})
        if mapping:
            normalized = {}
            for our_field, their_path in mapping.items():
                value = raw_data
                for key in their_path.split('.'):
                    if isinstance(value, dict):
                        value = value.get(key)
                    elif isinstance(value, list) and key.isdigit():
                        value = value[int(key)] if int(key) < len(value) else None
                    else:
                        value = None
                    if value is None:
                        break
                normalized[our_field] = value

            normalized['_raw'] = raw_data
            logs.append("✅ REST API response mapped successfully")
            return {"data": normalized, "logs": logs, "format": MCPFormat.REST_API}
        else:
            normalized = parse_mcp_response(raw_data, MCPFormat.REST_API)
            logs.append("✅ REST API response normalized")
            return {"data": normalized, "logs": logs, "format": MCPFormat.REST_API}

    except Exception as e:
        logs.append(f"❌ REST API failed: {str(e)}")
        return {"error": str(e), "logs": logs}


def parse_mcp_response(data, mcp_format):
    if not isinstance(data, dict):
        return {"error": "Invalid response format"}

    if mcp_format == MCPFormat.JSONRPC and "result" in data:
        data = data["result"]

    if mcp_format == MCPFormat.REST_API and "data" in data and isinstance(data["data"], dict):
        inner = data["data"]
        if any(k in inner for k in ["city", "weather", "aqi", "features", "place_name"]):
            data = inner

    normalized = {}

    normalized["city"] = data.get("city") or data.get("location") or data.get("name")
    if not normalized["city"]:
        features = data.get("features")
        if features and isinstance(features, list) and len(features) > 0:
            place = features[0]
            normalized["city"] = place.get("place_name") or place.get("text")
            if "center" in place:
                normalized["longitude"] = place["center"][0]
                normalized["latitude"] = place["center"][1]

    normalized["country"] = data.get("country") or data.get("country_code") or "Unknown"
    normalized["latitude"] = data.get("latitude") or data.get("lat")
    normalized["longitude"] = data.get("longitude") or data.get("lon") or data.get("lng")

    weather = data.get("weather") or data.get("current_weather") or data.get("current")
    if weather and isinstance(weather, dict):
        normalized["weather"] = {
            "temperature": weather.get("temperature") or weather.get("temp") or weather.get("temp2m"),
            "windspeed": weather.get("windspeed") or weather.get("wind_speed"),
            "winddirection": weather.get("winddirection") or weather.get("wind_direction"),
            "weathercode": weather.get("weathercode") or weather.get("weather_code", 0),
            "is_day": weather.get("is_day", 1),
            "time": weather.get("time") or data.get("current_time")
        }

    aqi = data.get("aqi") or data.get("air_quality")
    if aqi and isinstance(aqi, dict):
        normalized["aqi"] = {
            "us_aqi": aqi.get("us_aqi") or aqi.get("aqi") or aqi.get("pm25") or aqi.get("pm2_5"),
            "pm10": aqi.get("pm10"),
            "pm2_5": aqi.get("pm2_5") or aqi.get("pm25")
        }
    elif isinstance(aqi, (int, float)):
        normalized["aqi"] = {"us_aqi": aqi}

    normalized["current_time"] = data.get("current_time") or data.get("time") or data.get("local_time")

    special = data.get("today_special") or data.get("special") or {}
    if special and isinstance(special, dict):
        normalized["today_special"] = special

    for key, value in data.items():
        if key not in normalized and key not in ["weather", "aqi", "today_special", "current", "features"]:
            normalized[key] = value

    if not normalized.get("city"):
        normalized["_needs_mapping"] = True
        normalized["_raw_keys"] = list(data.keys())

    return normalized


# ==============================
# 🔗 MULTI-MCP MERGE LOGIC
# ==============================

def merge_mcp_data(primary_data, secondary_data):
    """
    Merge two MCP responses. Primary takes precedence.
    Secondary fills only missing/null fields.
    Preserves all source names across chained merges.
    """
    if not primary_data and not secondary_data:
        return None
    if not primary_data:
        return secondary_data
    if not secondary_data:
        return primary_data

    merged = dict(primary_data)

    # Collect existing sources from primary
    existing_sources = merged.get("_sources", [])
    if not existing_sources and (merged.get("source") or merged.get("_source")):
        existing_sources = [merged.get("source") or merged.get("_source")]

    # Collect new source from secondary
    new_source = secondary_data.get("source") or secondary_data.get("_source")

    # Top-level fields: fill if missing or "Unknown"
    fill_fields = ["country", "latitude", "longitude", "current_time"]
    for field in fill_fields:
        if field not in merged or merged[field] is None or merged[field] == "Unknown":
            if field in secondary_data and secondary_data[field] is not None:
                merged[field] = secondary_data[field]

    # Weather: merge individual sub-fields
    if "weather" in secondary_data and isinstance(secondary_data["weather"], dict):
        if "weather" not in merged or not isinstance(merged.get("weather"), dict):
            merged["weather"] = secondary_data["weather"]
        else:
            for wkey in ["temperature", "windspeed", "winddirection", "weathercode", "is_day", "time"]:
                if wkey not in merged["weather"] or merged["weather"][wkey] is None:
                    if wkey in secondary_data["weather"]:
                        merged["weather"][wkey] = secondary_data["weather"][wkey]

    # AQI: merge individual sub-fields
    if "aqi" in secondary_data and isinstance(secondary_data["aqi"], dict):
        if "aqi" not in merged or not isinstance(merged.get("aqi"), dict):
            merged["aqi"] = secondary_data["aqi"]
        else:
            for akey in ["us_aqi", "pm10", "pm2_5"]:
                if akey not in merged["aqi"] or merged["aqi"][akey] is None:
                    if akey in secondary_data["aqi"]:
                        merged["aqi"][akey] = secondary_data["aqi"][akey]

    # Today Special: merge holiday/fact
    if "today_special" in secondary_data and isinstance(secondary_data["today_special"], dict):
        if "today_special" not in merged or not isinstance(merged.get("today_special"), dict):
            merged["today_special"] = secondary_data["today_special"]
        else:
            for skey in ["holiday", "fact"]:
                if skey not in merged["today_special"] or not merged["today_special"][skey]:
                    if skey in secondary_data["today_special"] and secondary_data["today_special"][skey]:
                        merged["today_special"][skey] = secondary_data["today_special"][skey]

    # Merge and deduplicate sources
    all_sources = list(existing_sources)
    if new_source and new_source not in all_sources:
        all_sources.append(new_source)

    if all_sources:
        merged["_sources"] = all_sources

    return merged


def call_mcp_multi(tool, city, servers):
    """
    Call multiple MCP servers with fallback/merge.
    servers: list of dicts with 'url', 'config', 'name'
    Returns: {"data": merged, "logs": [...], "sources": [...]}
    """
    all_logs = []
    results = []

    for i, server in enumerate(servers):
        url = server.get("url")
        config = server.get("config")
        name = server.get("name", f"Server-{i+1}")

        all_logs.append(f"🔄 [{i+1}/{len(servers)}] Trying {name} @ {url}")
        result = call_mcp(tool, city, custom_url=url, server_config=config)
        all_logs.extend(result.get("logs", []))

        if "error" not in result and result.get("data"):
            results.append(result["data"])
            all_logs.append(f"✅ {name} returned data")
        else:
            err = result.get("error", "No data")
            all_logs.append(f"⚠️ {name} failed: {err}")

    if not results:
        return {"error": "All MCP servers failed", "logs": all_logs}

    # Chain merge all results
    merged = results[0]
    for i in range(1, len(results)):
        merged = merge_mcp_data(merged, results[i])

    return {
        "data": merged,
        "logs": all_logs,
        "sources": merged.get("_sources", [])
    }


# ==============================
# 🧠 TOOL SELECTION
# ==============================
def choose_tool(user_input):
    text = user_input.lower()
    if "aqi" in text or "air" in text:
        return "getAQI"
    elif "time" in text:
        return "getTimeOnly"
    elif "coordinate" in text:
        return "getCoordinatesOnly"
    elif "weather" in text:
        return "getWeatherOnly"
    elif "today" in text or "holiday" in text:
        return "getTodaySpecial"
    else:
        return "getFullInsights"


# ==============================
# 🌍 CITY EXTRACTION
# ==============================
def extract_cities(user_input):
    words = re.findall(r"[A-Za-z]+", user_input)
    ignore = {
        "weather", "today", "tell", "me", "what", "is", "the", "in", "show", "give",
        "details", "and", "compare", "vs", "versus", "between", "aqi", "air", "quality",
        "time", "coordinate", "holiday", "about", "how", "are", "you", "hi", "hello",
        "hey", "why", "when", "where", "who", "which", "can", "could", "would", "should",
        "will", "shall", "may", "might", "do", "does", "did", "have", "has", "had",
        "am", "was", "were", "been", "being", "get", "got", "for", "with", "from",
        "by", "on", "at", "to", "of", "as", "or", "but", "not", "no", "yes", "ok",
        "okay", "thanks", "thank", "please", "let", "know", "more", "some", "any"
    }
    cities = []
    for w in words:
        lower = w.lower()
        if lower not in ignore and len(w) > 2:
            if w[0].isupper() or w.isupper():
                cities.append(w.capitalize())
            elif lower in {"delhi", "mumbai", "kolkata", "chennai", "bangalore", "hyderabad",
                          "pune", "jaipur", "lucknow", "kanpur", "tokyo", "london", "paris",
                          "newyork", "dubai", "singapore", "sydney", "toronto", "berlin",
                          "madrid", "rome", "moscow", "beijing", "shanghai", "seoul",
                          "bangkok", "jakarta", "manila", "karachi", "istanbul", "cairo",
                          "lagos", "nairobi", "capetown", "rio", "santiago", "mexico",
                          "buenos", "aires", "lima", "dhaka", "colombo", "kathmandu",
                          "islamabad", "tehran", "baghdad", "riyadh", "doha", "kuwait"}:
                cities.append(w.capitalize())
    return list(dict.fromkeys(cities))


# ==============================
# 🔧 SINGLE MCP CALL (backward compat)
# ==============================
def call_mcp(tool, city, custom_url=None, server_config=None):
    logs = []
    url = custom_url or MCP_URL

    mcp_format, available_tools, detect_logs = detect_mcp_format(url)
    logs.extend(detect_logs)

    if mcp_format == MCPFormat.REST_API:
        return call_mcp_rest_api(url, tool, city, server_config, timeout=15)

    payload = build_mcp_payload(tool, city, mcp_format, available_tools, server_config)
    logs.append(f"📤 Payload ({mcp_format}): {json.dumps(payload, indent=2)}")

    try:
        log_msg = f"🔄 Calling MCP [{mcp_format}]: {tool} → {city} @ {url}"
        logs.append(log_msg)
        print(f"{Color.CYAN}{log_msg}{Color.END}")

        res = requests.post(url, json=payload, timeout=15)

        if res.status_code != 200:
            error_msg = f"❌ MCP failed: HTTP {res.status_code}"
            logs.append(error_msg)
            return {"error": f"HTTP {res.status_code}", "logs": logs}

        raw_data = res.json()
        if not raw_data:
            logs.append("❌ MCP failed: Empty response")
            return {"error": "Empty MCP response", "logs": logs}

        if mcp_format == MCPFormat.JSONRPC and "error" in raw_data:
            err = raw_data["error"]
            logs.append(f"❌ MCP JSON-RPC error: {err}")
            return {"error": str(err), "logs": logs}

        if "error" in raw_data and mcp_format != MCPFormat.JSONRPC:
            logs.append(f"❌ MCP failed: {raw_data['error']}")
            return {"error": raw_data["error"], "logs": logs}

        parsed_data = parse_mcp_response(raw_data, mcp_format)
        logs.append(f"✅ MCP success for {city} (format: {mcp_format})")
        return {"data": parsed_data, "logs": logs, "format": mcp_format}

    except requests.exceptions.Timeout:
        logs.append("❌ MCP failed: Timeout")
        return {"error": "Request timeout", "logs": logs}
    except Exception as e:
        logs.append(f"❌ MCP failed: {str(e)}")
        return {"error": str(e), "logs": logs}


# ==============================
# 🧹 CLEAN DATA
# ==============================
def clean_data(data):
    if not isinstance(data, dict):
        return {"error": "Invalid MCP format"}
    cleaned = {k: v for k, v in data.items() if v is not None}
    cleaned.setdefault("country", "Unknown")
    return cleaned


# ==============================
# 🧠 PROMPT BUILDERS
# ==============================
def build_city_prompt(user_query, mcp_data):
    return f"""You are GeoBot, a location intelligence assistant.
The user asked: "{user_query}"

Here is the real-time data for {mcp_data.get('city', 'the city')}:
{json.dumps(mcp_data, indent=2)}

Provide a friendly, informative response about this city. Include interesting facts, travel tips, or cultural insights. Keep it concise but engaging (3-5 sentences)."""

def build_city_prompt_multi(user_query, mcp_data, sources):
    sources_text = ", ".join(sources) if sources else "available sources"
    return f"""You are GeoBot, a location intelligence assistant.
The user asked: "{user_query}"

Here is the combined real-time data for {mcp_data.get('city', 'the city')} (merged from {sources_text}):
{json.dumps(mcp_data, indent=2)}

Provide a friendly, informative response. Mention that data was combined from multiple sources if relevant. Include interesting facts, travel tips, or cultural insights. Keep it concise but engaging (3-5 sentences)."""

def build_general_prompt(user_query):
    return f"""You are GeoBot, a helpful location intelligence assistant.
Answer the user's question clearly and concisely.

USER: {user_query}

Answer:"""

def build_compare_prompt_multi(results, sources_list):
    return f"""Compare these cities based on the combined data from multiple sources:
{json.dumps(results, indent=2)}

Sources used: {json.dumps(sources_list)}

Provide a brief comparison (2-3 sentences) highlighting key differences in weather, air quality, and any interesting observations."""


# ==============================
# 🤖 GROQ RESPONSES
# ==============================
def generate_llm_text(prompt):
    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are GeoBot, a friendly and knowledgeable location intelligence assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            stream=False
        )
        return completion.choices[0].message.content or ""
    except Exception as e:
        print(f"{Color.RED}❌ Groq Error: {e}{Color.END}")
        return ""


def generate_city_insights(user_query, mcp_data):
    prompt = build_city_prompt(user_query, mcp_data)
    return generate_llm_text(prompt)


def generate_city_insights_multi(user_query, mcp_data, sources):
    prompt = build_city_prompt_multi(user_query, mcp_data, sources)
    return generate_llm_text(prompt)


def generate_general_response(user_query):
    prompt = build_general_prompt(user_query)
    return generate_llm_text(prompt)


# ==============================
# 🚀 CLI
# ==============================
def start_cli():
    print(f"{Color.GREEN}{Color.BOLD}🚀 MCP + Groq Agent Ready{Color.END}")
    print(f"{Color.YELLOW}🔍 MCP Format Auto-Detection Enabled{Color.END}")
    print()
    while True:
        user_input = input(f"{Color.CYAN}{Color.BOLD}You:{Color.END} ")
        if user_input.lower() == "exit":
            print("👋 Bye!")
            break
        cities = extract_cities(user_input)
        if not cities:
            response = generate_general_response(user_input)
            print()
            print(f"{Color.GREEN}🤖 AI:{Color.END}")
            print(response)
            print()
            continue
        if len(cities) > 1:
            print(f"{Color.YELLOW}🔍 Multi-city mode{Color.END}")
            results = []
            for city in cities:
                result = call_mcp("getFullInsights", city)
                if "error" not in result:
                    results.append(clean_data(result["data"]))
            if not results:
                print("❌ No valid data")
                continue
            print()
            print(f"{Color.GREEN}🤖 AI:{Color.END}")
            print("Multi-city results received.")
            print()
        else:
            city = cities[0]
            tool = choose_tool(user_input)
            result = call_mcp(tool, city)
            if "error" in result:
                print(f"{Color.RED}{result['error']}{Color.END}")
                continue
            cleaned = clean_data(result["data"])
            insights = generate_city_insights(user_input, cleaned)
            print()
            print(f"{Color.GREEN}🤖 AI:{Color.END}")
            print(insights)
            print()

if __name__ == "__main__":
    start_cli()

def run_agent():
    start_cli()