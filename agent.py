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
MCP_URL = DEFAULT_MCP_URL  # can be overridden dynamically

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

if not GROQ_API_KEY:
    print("❌ Set GROQ_API_KEY environment variable first")
    raise SystemExit(1)

client = Groq(api_key=GROQ_API_KEY)
MODEL = "llama-3.1-8b-instant"


# ==============================
# 🔍 MCP FORMAT DETECTION SYSTEM
# ==============================

class MCPFormat:
    CUSTOM = "custom"           # {"tool": "...", "input": "..."}
    JSONRPC = "jsonrpc"         # {"jsonrpc":"2.0","method":"tools/call","params":{...}}
    OPENAI = "openai"           # {"function":"...","parameters":{...}}
    REST = "rest"               # {"action":"...","data":"..."}
    UNKNOWN = "unknown"

# Cache: url -> detected format
FORMAT_CACHE = {}

# Cache: url -> available tools list
TOOLS_CACHE = {}

def detect_mcp_format(url, timeout=10):
    """
    Auto-detect the MCP protocol format used by a server.
    Returns: (format_type, available_tools, logs)
    """
    logs = []
    
    # Check cache first
    if url in FORMAT_CACHE:
        logs.append(f"📋 Using cached format for {url}: {FORMAT_CACHE[url]}")
        return FORMAT_CACHE[url], TOOLS_CACHE.get(url, []), logs
    
    logs.append(f"🔍 Detecting MCP format for: {url}")
    print(f"{Color.YELLOW}🔍 Detecting MCP format for: {url}{Color.END}")
    
    detected_format = MCPFormat.UNKNOWN
    available_tools = []
    
    # --- Strategy 1: Try healthCheck in CUSTOM format ---
    try:
        custom_payload = {"tool": "healthCheck", "input": "test"}
        res = requests.post(url, json=custom_payload, timeout=timeout)
        
        if res.status_code == 200:
            data = res.json()
            # Check if response matches our custom format
            if "status" in data and "server" in data:
                detected_format = MCPFormat.CUSTOM
                logs.append("✅ Detected: CUSTOM format (your protocol)")
                print(f"{Color.GREEN}✅ Detected: CUSTOM format{Color.END}")
                # Try to extract tools list if available
                if "features" in data:
                    available_tools = list(data["features"].keys())
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs
            
            # Check if it's actually JSON-RPC response to our custom request
            if "jsonrpc" in data:
                detected_format = MCPFormat.JSONRPC
                logs.append("✅ Detected: JSON-RPC format (from healthCheck response)")
                print(f"{Color.GREEN}✅ Detected: JSON-RPC format{Color.END}")
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs
                
    except Exception as e:
        logs.append(f"⚠️ CUSTOM probe failed: {str(e)}")
    
    # --- Strategy 2: Try JSON-RPC format ---
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
                # Extract tools from result
                if "result" in data and "tools" in data["result"]:
                    available_tools = [t.get("name") for t in data["result"]["tools"] if t.get("name")]
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs
            # Some servers accept JSON-RPC but return plain JSON
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
    
    # --- Strategy 3: Try OpenAI-style format ---
    try:
        openai_payload = {
            "function": "list_tools",
            "parameters": {}
        }
        res = requests.post(url, json=openai_payload, timeout=timeout)
        
        if res.status_code == 200:
            data = res.json()
            if "functions" in data or "tools" in data:
                detected_format = MCPFormat.OPENAI
                logs.append("✅ Detected: OpenAI-style format")
                print(f"{Color.GREEN}✅ Detected: OpenAI-style format{Color.END}")
                if "tools" in data:
                    available_tools = [t.get("name") for t in data["tools"] if t.get("name")]
                elif "functions" in data:
                    available_tools = [f.get("name") for f in data["functions"] if f.get("name")]
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs
                
    except Exception as e:
        logs.append(f"⚠️ OpenAI probe failed: {str(e)}")
    
    # --- Strategy 4: Try generic REST format ---
    try:
        rest_payload = {
            "action": "health",
            "data": {}
        }
        res = requests.post(url, json=rest_payload, timeout=timeout)
        
        if res.status_code == 200:
            data = res.json()
            if "status" in data or "data" in data:
                detected_format = MCPFormat.REST
                logs.append("✅ Detected: Generic REST format")
                print(f"{Color.GREEN}✅ Detected: Generic REST format{Color.END}")
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs
                
    except Exception as e:
        logs.append(f"⚠️ REST probe failed: {str(e)}")
    
    # --- Fallback: Try GET request (some MCP servers use GET for discovery) ---
    try:
        res = requests.get(url, timeout=timeout)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict):
                if "jsonrpc" in data or "methods" in data:
                    detected_format = MCPFormat.JSONRPC
                elif "tools" in data or "functions" in data:
                    detected_format = MCPFormat.OPENAI
                else:
                    detected_format = MCPFormat.REST
                logs.append(f"✅ Detected: {detected_format.upper()} format (from GET)")
                print(f"{Color.GREEN}✅ Detected: {detected_format.upper()} format (from GET){Color.END}")
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs
                
    except Exception as e:
        logs.append(f"⚠️ GET probe failed: {str(e)}")
    
    # --- Final Fallback ---
    logs.append("⚠️ Could not auto-detect format. Defaulting to CUSTOM.")
    print(f"{Color.YELLOW}⚠️ Could not auto-detect format. Defaulting to CUSTOM.{Color.END}")
    detected_format = MCPFormat.CUSTOM
    FORMAT_CACHE[url] = detected_format
    TOOLS_CACHE[url] = available_tools
    return detected_format, available_tools, logs


def build_mcp_payload(tool, city, mcp_format, available_tools=None):
    """
    Build the correct request payload based on detected MCP format.
    Also maps tool names if the server uses different naming conventions.
    """
    
    # Map our tool names to server-specific names if needed
    tool_mapping = {
        "getFullInsights": "getFullInsights",
        "getWeatherOnly": "getWeatherOnly",
        "getAQI": "getAQI",
        "getTimeOnly": "getTimeOnly",
        "getCoordinatesOnly": "getCoordinatesOnly",
        "getTodaySpecial": "getTodaySpecial",
        "healthCheck": "healthCheck"
    }
    
    # If we know available tools, try to find the closest match
    if available_tools:
        tool_lower = tool.lower()
        for server_tool in available_tools:
            if tool_lower in server_tool.lower() or server_tool.lower() in tool_lower:
                tool_mapping[tool] = server_tool
                break
    
    actual_tool = tool_mapping.get(tool, tool)
    
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
    
    elif mcp_format == MCPFormat.OPENAI:
        return {
            "function": actual_tool,
            "parameters": {
                "city": city,
                "location": city,
                "input": city
            }
        }
    
    elif mcp_format == MCPFormat.REST:
        return {
            "action": actual_tool,
            "data": {
                "city": city,
                "location": city,
                "input": city
            }
        }
    
    else:  # CUSTOM (default)
        return {
            "tool": actual_tool,
            "input": city
        }


def parse_mcp_response(data, mcp_format):
    """
    Parse and normalize MCP response regardless of format.
    Returns standardized dict with city, weather, aqi, etc.
    """
    if not isinstance(data, dict):
        return {"error": "Invalid response format"}
    
    # JSON-RPC wrapper unwrap
    if mcp_format == MCPFormat.JSONRPC and "result" in data:
        data = data["result"]
    
    # OpenAI wrapper unwrap
    if mcp_format == MCPFormat.OPENAI and "output" in data:
        data = data["output"]
    
    # REST wrapper unwrap
    if mcp_format == MCPFormat.REST and "data" in data:
        data = data["data"]
    
    # Normalize common field names
    normalized = {}
    
    # City / Location
    normalized["city"] = data.get("city") or data.get("location") or data.get("name") or "Unknown"
    normalized["country"] = data.get("country") or data.get("country_code") or "Unknown"
    normalized["latitude"] = data.get("latitude") or data.get("lat")
    normalized["longitude"] = data.get("longitude") or data.get("lon") or data.get("lng")
    
    # Weather (handle nested or flat structures)
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
    
    # AQI
    aqi = data.get("aqi") or data.get("air_quality") or data.get("current", {}).get("us_aqi")
    if aqi and isinstance(aqi, dict):
        normalized["aqi"] = {
            "us_aqi": aqi.get("us_aqi") or aqi.get("aqi") or aqi.get("pm25") or aqi.get("pm2_5"),
            "pm10": aqi.get("pm10"),
            "pm2_5": aqi.get("pm2_5") or aqi.get("pm25")
        }
    elif isinstance(aqi, (int, float)):
        normalized["aqi"] = {"us_aqi": aqi}
    
    # Time
    normalized["current_time"] = data.get("current_time") or data.get("time") or data.get("local_time")
    
    # Special (holidays, facts)
    special = data.get("today_special") or data.get("special") or {}
    if special and isinstance(special, dict):
        normalized["today_special"] = special
    
    # Copy any other fields not yet mapped
    for key, value in data.items():
        if key not in normalized and key not in ["weather", "aqi", "today_special", "current"]:
            normalized[key] = value
    
    return normalized


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
# 🔧 MCP CALL WITH FORMAT DETECTION
# ==============================
def call_mcp(tool, city, custom_url=None):
    logs = []
    url = custom_url or MCP_URL
    
    # Step 1: Detect format (cached after first call)
    mcp_format, available_tools, detect_logs = detect_mcp_format(url)
    logs.extend(detect_logs)
    
    # Step 2: Build correct payload
    payload = build_mcp_payload(tool, city, mcp_format, available_tools)
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
        
        # Handle JSON-RPC errors
        if mcp_format == MCPFormat.JSONRPC and "error" in raw_data:
            err = raw_data["error"]
            logs.append(f"❌ MCP JSON-RPC error: {err}")
            return {"error": str(err), "logs": logs}
        
        # Handle generic error field
        if "error" in raw_data and mcp_format != MCPFormat.JSONRPC:
            logs.append(f"❌ MCP failed: {raw_data['error']}")
            return {"error": raw_data["error"], "logs": logs}

        # Step 3: Parse and normalize response
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

def build_general_prompt(user_query):
    return f"""You are GeoBot, a helpful location intelligence assistant.
Answer the user's question clearly and concisely.

USER: {user_query}

Answer:"""


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