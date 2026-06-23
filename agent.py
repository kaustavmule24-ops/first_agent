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
MCP_URL = "https://mcp-weather-s1s0.onrender.com/tool"

# ✅ USE ENV VARIABLE (IMPORTANT)
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
# 🌍 CITY EXTRACTION (IMPROVED)
# ==============================
def extract_cities(user_input):
    words = re.findall(r"[A-Za-z]+", user_input)

    ignore = {
        "weather", "today", "tell", "me", "what", "is",
        "the", "in", "show", "give", "details", "and",
        "compare", "vs", "versus", "between", "aqi", "air",
        "quality", "time", "coordinate", "holiday", "about",
        "how", "are", "you", "hi", "hello", "hey", "what",
        "why", "when", "where", "who", "which", "can", "could",
        "would", "should", "will", "shall", "may", "might",
        "do", "does", "did", "have", "has", "had", "am",
        "was", "were", "been", "being", "get", "got",
        "for", "with", "from", "by", "on", "at", "to",
        "of", "as", "or", "and", "but", "not", "no",
        "yes", "ok", "okay", "thanks", "thank", "please"
    }

    cities = []
    for w in words:
        lower = w.lower()
        if lower not in ignore and len(w) > 2:
            # Check if it looks like a city (capitalized or all caps)
            if w[0].isupper() or w.isupper():
                cities.append(w.capitalize())
            # Also accept common city names even if lowercase
            elif lower in {"delhi", "mumbai", "kolkata", "chennai", "bangalore", 
                          "hyderabad", "pune", "jaipur", "lucknow", "kanpur",
                          "tokyo", "london", "paris", "newyork", "dubai",
                          "singapore", "sydney", "toronto", "berlin", "madrid",
                          "rome", "moscow", "beijing", "shanghai", "seoul",
                          "bangkok", "jakarta", "manila", "karachi", "istanbul",
                          "cairo", "lagos", "nairobi", "capetown", "rio",
                          "santiago", "mexico", "buenos", "aires", "lima"}:
                cities.append(w.capitalize())

    print(f"{Color.YELLOW}🔍 Detected cities: {cities}{Color.END}")
    return list(dict.fromkeys(cities))  # Remove duplicates while preserving order


# ==============================
# 🔧 MCP CALL WITH LOGS
# ==============================
def call_mcp(tool, city):
    logs = []
    try:
        log_msg = f"🔄 Calling MCP: {tool} → {city}"
        logs.append(log_msg)
        print(f"{Color.CYAN}{log_msg}{Color.END}")

        res = requests.post(
            MCP_URL,
            json={"tool": tool, "input": city},
            timeout=15
        )

        if res.status_code != 200:
            error_msg = f"❌ MCP failed: HTTP {res.status_code}"
            logs.append(error_msg)
            print(f"{Color.RED}{error_msg}{Color.END}")
            return {"error": f"HTTP {res.status_code}", "logs": logs}

        data = res.json()

        if not data:
            error_msg = "❌ MCP failed: Empty response"
            logs.append(error_msg)
            print(f"{Color.RED}{error_msg}{Color.END}")
            return {"error": "Empty MCP response", "logs": logs}

        if "error" in data:
            error_msg = f"❌ MCP failed: {data['error']}"
            logs.append(error_msg)
            print(f"{Color.RED}{error_msg}{Color.END}")
            return {"error": data["error"], "logs": logs}

        success_msg = f"✅ MCP success for {city}"
        logs.append(success_msg)
        print(f"{Color.GREEN}{success_msg}{Color.END}")
        return {"data": data, "logs": logs}

    except requests.exceptions.Timeout:
        error_msg = "❌ MCP failed: Timeout"
        logs.append(error_msg)
        print(f"{Color.RED}{error_msg}{Color.END}")
        return {"error": "Request timeout", "logs": logs}
    except Exception as e:
        error_msg = f"❌ MCP failed: {str(e)}"
        logs.append(error_msg)
        print(f"{Color.RED}{error_msg}{Color.END}")
        return {"error": str(e), "logs": logs}


# ==============================
# 🧹 CLEAN DATA
# ==============================
def clean_data(data):
    if not isinstance(data, dict):
        return {"error": "Invalid MCP format"}

    cleaned = {}

    for k, v in data.items():
        if v is None:
            continue
        cleaned[k] = v

    cleaned.setdefault("country", "Unknown")

    return cleaned


# ==============================
# 🧠 PROMPT BUILDERS
# ==============================
def build_prompt(user_query, mcp_data):
    return f"""
FORMAT STRICT:

📍 Location: City, Country
🌡 Weather:
🌫 Air Quality:
🕒 Time:
🎉 Highlights:

DATA:
{json.dumps(mcp_data, indent=2)}

USER:
{user_query}
"""


def build_general_prompt(user_query):
    return f"""You are GeoBot, a friendly location intelligence assistant. 
Answer the user's question clearly and concisely. If the question is about locations, weather, or geography, provide helpful context.

USER: {user_query}

Answer:"""


# ==============================
# 🤖 GROQ RESPONSE — FORMATTED DATA
# ==============================
def generate_llm_response(prompt):
    try:
        print(f"{Color.GREEN}⚡ Generating formatted response...{Color.END}")

        completion = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "Format data cleanly with emojis and clear sections."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            stream=False
        )

        full = completion.choices[0].message.content

        if not full:
            print("❌ Empty LLM response")
            return ""

        print("\n")
        return full

    except Exception as e:
        print(f"{Color.RED}❌ Groq Error: {e}{Color.END}")
        return ""


# ==============================
# 🤖 GROQ RESPONSE — GENERAL TEXT
# ==============================
def generate_llm_text(user_query):
    """For general questions without city context"""
    try:
        print(f"{Color.GREEN}⚡ Generating general response...{Color.END}")

        prompt = build_general_prompt(user_query)

        completion = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are GeoBot, a helpful location intelligence assistant. Answer clearly and concisely."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            stream=False
        )

        full = completion.choices[0].message.content

        if not full:
            return "I'm not sure how to answer that. Try asking about a city's weather or air quality!"

        return full

    except Exception as e:
        print(f"{Color.RED}❌ Groq Error: {e}{Color.END}")
        return "Sorry, I encountered an error. Please try again."


# ==============================
# 🚀 CLI
# ==============================
def start_cli():
    print(f"{Color.GREEN}{Color.BOLD}🚀 MCP + Groq Agent Ready{Color.END}\n")

    while True:
        user_input = input(f"{Color.CYAN}{Color.BOLD}You:{Color.END} ")

        if user_input.lower() == "exit":
            print("👋 Bye!")
            break

        cities = extract_cities(user_input)

        if not cities:
            # No city found → General LLM response
            print(f"{Color.YELLOW}🤖 No city detected. Using general LLM...{Color.END}")
            response = generate_llm_text(user_input)
            print(f"\n{Color.GREEN}🤖 AI:{Color.END}\n{response}\n")
            continue

        if len(cities) > 1:
            print(f"{Color.YELLOW}🔍 Multi-city mode{Color.END}")

            results = []
            all_logs = []
            for city in cities:
                result = call_mcp("getFullInsights", city)
                all_logs.extend(result.get("logs", []))

                if "error" in result:
                    print(f"{Color.RED}{city}: {result['error']}{Color.END}")
                    continue

                results.append({city: clean_data(result["data"])})

            if not results:
                print("❌ No valid data")
                continue

            prompt = build_prompt(user_input, results)

        else:
            city = cities[0]
            tool = choose_tool(user_input)

            result = call_mcp(tool, city)

            if "error" in result:
                print(f"{Color.RED}{result['error']}{Color.END}")
                continue

            prompt = build_prompt(user_input, clean_data(result["data"]))

        print(f"\n{Color.GREEN}🤖 AI:{Color.END}\n")
        generate_llm_response(prompt)


# ==============================
# ▶️ ENTRY
# ==============================
if __name__ == "__main__":
    start_cli()

# ==============================
def run_agent():
    start_cli()
