import requests
import json
import re
import os
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
# 🔧 MCP CALL WITH LOGS
# ==============================
def call_mcp(tool, city, custom_url=None):
    logs = []
    url = custom_url or MCP_URL
    
    try:
        log_msg = f"🔄 Calling MCP: {tool} → {city} @ {url}"
        logs.append(log_msg)
        print(f"{Color.CYAN}{log_msg}{Color.END}")

        res = requests.post(url, json={"tool": tool, "input": city}, timeout=15)

        if res.status_code != 200:
            error_msg = f"❌ MCP failed: HTTP {res.status_code}"
            logs.append(error_msg)
            return {"error": f"HTTP {res.status_code}", "logs": logs}

        data = res.json()
        if not data:
            logs.append("❌ MCP failed: Empty response")
            return {"error": "Empty MCP response", "logs": logs}
        if "error" in data:
            logs.append(f"❌ MCP failed: {data['error']}")
            return {"error": data["error"], "logs": logs}

        logs.append(f"✅ MCP success for {city}")
        return {"data": data, "logs": logs}

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
