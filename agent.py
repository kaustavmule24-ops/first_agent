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
    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"
    UNKNOWN = "unknown"

FORMAT_CACHE = {}
TOOLS_CACHE = {}


# ==============================
# 🌐 PROTOCOL DETECTION ENGINE
# ==============================

def detect_mcp_format(url, timeout=10, auth_token=None):
    logs = []

    if url in FORMAT_CACHE:
        logs.append(f"📋 Using cached format for {url}: {FORMAT_CACHE[url]}")
        return FORMAT_CACHE[url], TOOLS_CACHE.get(url, []), logs

    logs.append(f"🔍 Detecting MCP format for: {url}")
    print(f"{Color.YELLOW}🔍 Detecting MCP format for: {url}{Color.END}")

    detected_format = MCPFormat.UNKNOWN
    available_tools = []

    # Build headers with auth if available
    probe_headers = {"Content-Type": "application/json"}
    if auth_token:
        probe_headers["Authorization"] = f"Bearer {auth_token}"

    # Strategy 1: CUSTOM format probe
    try:
        custom_payload = {"tool": "healthCheck", "input": "test"}
        res = requests.post(url, json=custom_payload, headers=probe_headers, timeout=timeout)

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
            # Echo server returns whatever we send - detect by echo pattern
            if data == custom_payload or (isinstance(data, dict) and data.get("tool") == "healthCheck"):
                detected_format = MCPFormat.JSONRPC
                logs.append("✅ Detected: JSON-RPC format (echo server - returns payload)")
                print(f"{Color.GREEN}✅ Detected: JSON-RPC format (echo server){Color.END}")
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs

    except Exception as e:
        logs.append(f"⚠️ CUSTOM probe failed: {str(e)}")

    # Strategy 2: JSON-RPC format probe (with proper MCP initialize handshake)
    try:
        # First try initialize (proper MCP handshake)
        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "geobot", "version": "1.0"}
            }
        }
        res = requests.post(url, json=init_payload, headers=probe_headers, timeout=timeout)

        if res.status_code == 200:
            data = res.json()
            if "jsonrpc" in data and ("result" in data or "error" in data):
                detected_format = MCPFormat.JSONRPC
                logs.append("✅ Detected: JSON-RPC format (MCP initialize handshake)")
                print(f"{Color.GREEN}✅ Detected: JSON-RPC format{Color.END}")
                # Try to get tools list
                try:
                    tools_payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
                    tools_res = requests.post(url, json=tools_payload, timeout=timeout)
                    if tools_res.status_code == 200:
                        tools_data = tools_res.json()
                        if "result" in tools_data and "tools" in tools_data["result"]:
                            available_tools = [t.get("name") for t in tools_data["result"]["tools"] if t.get("name")]
                except:
                    pass
                FORMAT_CACHE[url] = detected_format
                TOOLS_CACHE[url] = available_tools
                return detected_format, available_tools, logs

        # Fallback: try tools/list directly (some servers don't need init)
        jsonrpc_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {}
        }
        res = requests.post(url, json=jsonrpc_payload, headers=probe_headers, timeout=timeout)

        if res.status_code == 200:
            data = res.json()
            if "jsonrpc" in data and ("result" in data or "error" in data):
                detected_format = MCPFormat.JSONRPC
                logs.append("✅ Detected: JSON-RPC format (tools/list response)")
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
        base_url = url.replace('/tool', '').replace('/mcp', '').rstrip('/')
        res = requests.get(base_url, headers=probe_headers, timeout=timeout)
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
        # Also detect REST API by URL patterns (e.g., api.mapbox.com, api.openweathermap.org)
        if any(domain in base_url for domain in ['api.', 'rest.', 'data.', 'maps.']):
            detected_format = MCPFormat.REST_API
            logs.append(f"✅ Detected: REST API format (URL pattern match: {base_url})")
            print(f"{Color.GREEN}✅ Detected: REST API format (URL pattern){Color.END}")
            FORMAT_CACHE[url] = detected_format
            TOOLS_CACHE[url] = available_tools
            return detected_format, available_tools, logs

    except Exception as e:
        logs.append(f"⚠️ REST API probe failed: {str(e)}")

    # Strategy 4: Error response probe
    try:
        res = requests.post(url, headers=probe_headers, timeout=timeout)
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

    # Strategy 5: Stdio pseudo-URL
    if url.startswith("stdio://"):
        detected_format = MCPFormat.STDIO
        logs.append("✅ Detected: STDIO format (local subprocess)")
        print(f"{Color.GREEN}✅ Detected: STDIO format{Color.END}")
        FORMAT_CACHE[url] = detected_format
        TOOLS_CACHE[url] = available_tools
        return detected_format, available_tools, logs

    # Strategy 6: Streamable HTTP (MCP 2025-03-26 spec)
    # Apify, Cloudflare, and modern MCP servers use this
    if any(domain in url for domain in ['mcp.apify.com', 'mcp.cloudflare.com']):
        detected_format = MCPFormat.STREAMABLE_HTTP
        logs.append(f"✅ Detected: Streamable HTTP format ({url})")
        print(f"{Color.GREEN}✅ Detected: Streamable HTTP format{Color.END}")
        FORMAT_CACHE[url] = detected_format
        TOOLS_CACHE[url] = available_tools
        return detected_format, available_tools, logs

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




def call_mcp_stdio(tool, city, server_config, timeout=15, auth_token=None):
    """
    Call an MCP server via stdio (subprocess).
    Uses JSON-RPC 2.0 over stdin/stdout.
    """
    import subprocess
    import select
    import time

    logs = []
    config = server_config or {}
    cmd = config.get("command", "tsx")
    args = config.get("args", [])
    cwd = config.get("cwd") or None

    full_cmd = [cmd] + args
    logs.append(f"💻 Spawning stdio MCP: {' '.join(full_cmd)}")
    print(f"{Color.CYAN}💻 Stdio MCP: {' '.join(full_cmd)}{Color.END}")

    try:
        proc = subprocess.Popen(
            full_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
            bufsize=1
        )

        # --- Initialize ---
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "geobot", "version": "1.0"}
            }
        }
        proc.stdin.write(json.dumps(init_req) + "\n")
        proc.stdin.flush()

        ready, _, _ = select.select([proc.stdout], [], [], timeout)
        if not ready:
            proc.terminate()
            logs.append("❌ Stdio init timeout")
            return {"error": "Stdio initialization timeout", "logs": logs}

        init_resp = json.loads(proc.stdout.readline())
        server_name = init_resp.get("result", {}).get("serverInfo", {}).get("name", "unknown")
        logs.append(f"✅ Stdio MCP initialized: {server_name}")

        # --- Send initialized notification ---
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        # --- Get tools list (optional but good for mapping) ---
        tools_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        proc.stdin.write(json.dumps(tools_req) + "\n")
        proc.stdin.flush()

        ready, _, _ = select.select([proc.stdout], [], [], 3)
        if ready:
            tools_resp = json.loads(proc.stdout.readline())
            available_tools = [t.get("name") for t in tools_resp.get("result", {}).get("tools", []) if t.get("name")]
            if available_tools:
                logs.append(f"📋 Tools: {', '.join(available_tools)}")

        # --- Call tool ---
        actual_tool = tool
        if available_tools:
            tool_lower = tool.lower()
            for t in available_tools:
                if tool_lower in t.lower() or t.lower() in tool_lower:
                    actual_tool = t
                    break

        call_req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": actual_tool,
                "arguments": {"city": city, "location": city, "input": city}
            }
        }
        proc.stdin.write(json.dumps(call_req) + "\n")
        proc.stdin.flush()

        ready, _, _ = select.select([proc.stdout], [], [], timeout)
        if not ready:
            proc.terminate()
            logs.append("❌ Stdio tool call timeout")
            return {"error": "Stdio tool call timeout", "logs": logs}

        resp_line = proc.stdout.readline()
        resp = json.loads(resp_line)
        proc.terminate()

        if "error" in resp:
            logs.append(f"❌ Stdio error: {resp['error']}")
            return {"error": resp["error"], "logs": logs}

        # Parse result
        result = resp.get("result", {})
        content = result.get("content", [])
        if content and len(content) > 0:
            text_content = content[0].get("text", "{}")
            try:
                data = json.loads(text_content)
                parsed = parse_mcp_response(data, MCPFormat.JSONRPC)
                logs.append("✅ Stdio response parsed successfully")
                return {"data": parsed, "logs": logs, "format": MCPFormat.STDIO}
            except json.JSONDecodeError:
                # Some stdio servers return plain text
                parsed = {
                    "city": city,
                    "country": "Unknown",
                    "current_time": "",
                    "weather": {"temperature": 0, "weathercode": 0, "is_day": 1},
                    "aqi": {"us_aqi": 0},
                    "_text_response": text_content
                }
                logs.append("✅ Stdio response received (text format)")
                return {"data": parsed, "logs": logs, "format": MCPFormat.STDIO}

        logs.append("✅ Stdio response received")
        return {"data": parse_mcp_response(result, MCPFormat.JSONRPC), "logs": logs, "format": MCPFormat.STDIO}

    except FileNotFoundError:
        logs.append(f"❌ Command not found: {cmd}")
        return {"error": f"Command not found: {cmd}. Make sure it is installed.", "logs": logs}
    except Exception as e:
        logs.append(f"❌ Stdio failed: {str(e)}")
        return {"error": str(e), "logs": logs}



def call_mcp_streamable_http(url, tool, city, server_config, timeout=15, auth_token=None):
    """
    Call an MCP server using Streamable HTTP transport (MCP spec 2025-03-26).
    Used by Apify, Cloudflare, and other modern MCP hosts.
    Requires Authorization header with Bearer token or OAuth.
    """
    logs = []
    config = server_config or {}

    logs.append(f"🌐 Streamable HTTP: {url}")
    print(f"{Color.CYAN}🌐 Streamable HTTP MCP: {url}{Color.END}")

    try:
        # Build auth headers
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
            'MCP-Protocol-Version': '2025-03-26'
        }
        if auth_token:
            masked = auth_token[:8] + "..." if len(auth_token) > 12 else "***"
            logs.append(f"🔐 [AUTH] Streamable HTTP Bearer: {masked}")
            headers['Authorization'] = f'Bearer {auth_token}'

        auth_type = config.get('auth_type', 'none')
        auth_value = config.get('auth_value', '')

        if auth_type == 'bearer' and auth_value:
            headers['Authorization'] = f'Bearer {auth_value}'
            logs.append("🔑 Auth: Bearer token")
        elif auth_type == 'header':
            auth_key = config.get('auth_key', '')
            if auth_key and auth_value:
                headers[auth_key] = auth_value
                logs.append(f"🔑 Auth: Header {auth_key}")

        # --- Step 1: Initialize ---
        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {
                    "tools": {},
                    "resources": {},
                    "prompts": {}
                },
                "clientInfo": {
                    "name": "geobot",
                    "version": "1.0.0"
                }
            }
        }

        logs.append("📤 Sending initialize...")
        res = requests.post(url, headers=headers, json=init_payload, timeout=timeout)
        logs.append(f"⬅️ Init status: {res.status_code}")

        if res.status_code not in [200, 202]:
            return {"error": f"Initialize failed: HTTP {res.status_code}", "logs": logs}

        # Parse init response (could be JSON or SSE)
        init_data = None
        content_type = res.headers.get('Content-Type', '')
        if 'text/event-stream' in content_type:
            # Parse SSE
            for line in res.text.split('\n'):
                if line.startswith('data: '):
                    init_data = json.loads(line[6:])
                    break
        else:
            init_data = res.json()

        if init_data and "result" in init_data:
            server_info = init_data["result"].get("serverInfo", {})
            logs.append(f"✅ Initialized: {server_info.get('name', 'unknown')} v{server_info.get('version', '?')}")

        # --- Step 2: Send initialized notification ---
        notify_payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized"
        }
        requests.post(url, headers=headers, json=notify_payload, timeout=5)

        # --- Step 3: List tools ---
        tools_payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }

        res = requests.post(url, headers=headers, json=tools_payload, timeout=timeout)
        available_tools = []
        if res.status_code == 200:
            tools_data = res.json()
            if "result" in tools_data and "tools" in tools_data["result"]:
                available_tools = [t.get("name") for t in tools_data["result"]["tools"] if t.get("name")]
                logs.append(f"📋 Tools: {', '.join(available_tools[:10])}")

        # --- Step 4: Call tool ---
        # Map our tool names to Apify tool names
        actual_tool = tool
        tool_lower = tool.lower()
        for t in available_tools:
            if tool_lower in t.lower() or t.lower() in tool_lower:
                actual_tool = t
                break

        # For Apify, tools are Actors. Common ones:
        if not available_tools and "apify" in url:
            # Default Apify tools if discovery fails
            available_tools = [
                "apify/rag-web-browser",
                "actors",
                "docs"
            ]

        call_payload = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": actual_tool,
                "arguments": {
                    "city": city,
                    "location": city,
                    "input": city,
                    "query": f"weather in {city}"
                }
            }
        }

        logs.append(f"📤 Calling tool: {actual_tool}")
        res = requests.post(url, headers=headers, json=call_payload, timeout=timeout)
        logs.append(f"⬅️ Tool call status: {res.status_code}")

        if res.status_code != 200:
            return {"error": f"Tool call failed: HTTP {res.status_code}", "logs": logs}

        # Parse response
        response_data = None
        content_type = res.headers.get('Content-Type', '')
        if 'text/event-stream' in content_type:
            # Parse SSE stream
            for line in res.text.split('\n'):
                if line.startswith('data: '):
                    try:
                        response_data = json.loads(line[6:])
                        if "result" in response_data or "error" in response_data:
                            break
                    except:
                        continue
        else:
            response_data = res.json()

        if not response_data:
            return {"error": "Empty response from Streamable HTTP", "logs": logs}

        if "error" in response_data:
            logs.append(f"❌ Tool error: {response_data['error']}")
            return {"error": response_data["error"], "logs": logs}

        # Extract result content
        result = response_data.get("result", {})
        content = result.get("content", [])

        if content and len(content) > 0:
            text_content = content[0].get("text", "{}")
            try:
                data = json.loads(text_content)
                parsed = parse_mcp_response(data, MCPFormat.JSONRPC)
                logs.append("✅ Streamable HTTP response parsed")
                return {"data": parsed, "logs": logs, "format": MCPFormat.STREAMABLE_HTTP}
            except json.JSONDecodeError:
                # Return as text insight
                parsed = {
                    "city": city,
                    "country": "Unknown",
                    "current_time": "",
                    "weather": {"temperature": 0, "weathercode": 0, "is_day": 1},
                    "aqi": {"us_aqi": 0},
                    "_text_response": text_content
                }
                logs.append("✅ Streamable HTTP response received (text)")
                return {"data": parsed, "logs": logs, "format": MCPFormat.STREAMABLE_HTTP}

        # Try to parse result directly
        parsed = parse_mcp_response(result, MCPFormat.JSONRPC)
        logs.append("✅ Streamable HTTP response parsed (direct)")
        return {"data": parsed, "logs": logs, "format": MCPFormat.STREAMABLE_HTTP}

    except requests.exceptions.Timeout:
        logs.append("❌ Streamable HTTP timeout")
        return {"error": "Streamable HTTP timeout", "logs": logs}
    except Exception as e:
        logs.append(f"❌ Streamable HTTP error: {str(e)}")
        return {"error": str(e), "logs": logs}

def call_mcp_rest_api(url, tool, city, server_config, timeout=15, auth_token=None):
    logs = []
    config = server_config or {}

    try:
        endpoint = config.get('endpoint_template', '/')
        endpoint = endpoint.replace('{city}', city)
        endpoint = endpoint.replace('{tool}', tool)

        # Build base URL: strip /tool if present, then append endpoint
        base_url = url.replace('/tool', '').rstrip('/')
        if not endpoint.startswith('/'):
            endpoint = '/' + endpoint
        full_url = base_url + endpoint

        logs.append(f"🌐 REST API call: {config.get('method', 'GET')} {full_url}")
        print(f"{Color.CYAN}🌐 REST API: {full_url}{Color.END}")

        headers = {'Content-Type': 'application/json'}
        if auth_token:
            masked = auth_token[:8] + "..." if len(auth_token) > 12 else "***"
            logs.append(f"🔐 [AUTH] REST API Bearer: {masked}")
            headers['Authorization'] = f'Bearer {auth_token}'
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
            logs.append(f"🔑 Auth: query param {auth_key}=***")

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
        logs.append(f"📥 Raw response keys: {list(raw_data.keys())[:10]}")

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
            # Auto-detect common REST API response patterns
            normalized = auto_normalize_rest_response(raw_data)
            if normalized:
                logs.append("✅ REST API response auto-normalized")
                return {"data": normalized, "logs": logs, "format": MCPFormat.REST_API}
            else:
                normalized = parse_mcp_response(raw_data, MCPFormat.REST_API)
                logs.append("✅ REST API response normalized (fallback)")
                return {"data": normalized, "logs": logs, "format": MCPFormat.REST_API}

    except Exception as e:
        logs.append(f"❌ REST API failed: {str(e)}")
        return {"error": str(e), "logs": logs}


def parse_mcp_response(data, mcp_format):
    if not isinstance(data, dict):
        return {"error": "Invalid response format"}

    # Handle JSON-RPC result wrapper (tool call responses)
    if mcp_format == MCPFormat.JSONRPC and "result" in data:
        result = data["result"]
        # If result has content array (proper MCP tool response), extract text
        if isinstance(result, dict) and "content" in result and isinstance(result["content"], list):
            text_content = result["content"][0].get("text", "{}") if result["content"] else "{}"
            try:
                inner_data = json.loads(text_content)
                if isinstance(inner_data, dict):
                    data = inner_data
                else:
                    data = {"_text_response": text_content}
            except json.JSONDecodeError:
                data = {"_text_response": text_content}
        else:
            data = result

    # Handle JSON-RPC error responses from test servers
    if mcp_format == MCPFormat.JSONRPC and "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            return {"error": err.get("message", str(err)), "city": "Unknown", "country": "Error"}
        return {"error": str(err), "city": "Unknown", "country": "Error"}

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
        # Try to extract city from common REST API patterns
        if "query" in data and isinstance(data["query"], list):
            normalized["city"] = data["query"][0] if data["query"] else None
        elif "place_name" in data:
            normalized["city"] = data["place_name"]
        elif "text" in data:
            normalized["city"] = data["text"]

        # If still no city, store minimal info but DON'T pollute with _raw_keys
        if not normalized.get("city"):
            normalized["city"] = "Unknown"
            normalized["_raw_preview"] = str(list(data.keys())[:5])

    return normalized



def auto_normalize_rest_response(raw_data):
    """
    Auto-detect and normalize common REST API response patterns.
    Returns normalized dict or None if pattern not recognized.
    """
    if not isinstance(raw_data, dict):
        return None

    normalized = {}

    # Pattern 1: Mapbox Geocoding API
    if "type" in raw_data and raw_data.get("type") == "FeatureCollection":
        features = raw_data.get("features", [])
        if features and len(features) > 0:
            feature = features[0]
            place_name = feature.get("place_name", "")
            # Extract city from place_name (usually first part)
            city_name = place_name.split(",")[0].strip() if "," in place_name else place_name

            normalized["city"] = city_name
            normalized["country"] = "Unknown"  # Could extract from context

            center = feature.get("center", [])
            if len(center) >= 2:
                normalized["longitude"] = center[0]
                normalized["latitude"] = center[1]

            normalized["current_time"] = ""
            normalized["weather"] = {"temperature": 0, "weathercode": 0, "is_day": 1}
            normalized["aqi"] = {"us_aqi": 0}

            return normalized

    # Pattern 2: OpenWeatherMap / standard weather APIs
    if "coord" in raw_data or "main" in raw_data:
        normalized["city"] = raw_data.get("name", "Unknown")
        normalized["country"] = raw_data.get("sys", {}).get("country", "Unknown")
        normalized["latitude"] = raw_data.get("coord", {}).get("lat")
        normalized["longitude"] = raw_data.get("coord", {}).get("lon")

        main = raw_data.get("main", {})
        weather_list = raw_data.get("weather", [])
        weather_code = weather_list[0].get("id", 0) if weather_list else 0

        normalized["weather"] = {
            "temperature": main.get("temp"),
            "windspeed": raw_data.get("wind", {}).get("speed"),
            "winddirection": raw_data.get("wind", {}).get("deg"),
            "weathercode": weather_code,
            "is_day": 1,
            "humidity": main.get("humidity"),
            "pressure": main.get("pressure")
        }
        normalized["aqi"] = {"us_aqi": 0}
        normalized["current_time"] = ""

        return normalized

    # Pattern 3: Generic geo data with lat/lon
    if any(k in raw_data for k in ["lat", "latitude", "lon", "longitude"]):
        normalized["city"] = raw_data.get("name") or raw_data.get("city") or "Unknown"
        normalized["country"] = raw_data.get("country") or "Unknown"
        normalized["latitude"] = raw_data.get("lat") or raw_data.get("latitude")
        normalized["longitude"] = raw_data.get("lon") or raw_data.get("longitude") or raw_data.get("lng")
        normalized["current_time"] = ""
        normalized["weather"] = {"temperature": 0, "weathercode": 0, "is_day": 1}
        normalized["aqi"] = {"us_aqi": 0}
        return normalized

    return None

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


def call_mcp_multi(tool, city, servers, auth_token=None):
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
        result = call_mcp(tool, city, custom_url=url, server_config=config, auth_token=auth_token)
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
def call_mcp(tool, city, custom_url=None, server_config=None, auth_token=None):
    logs = []
    url = custom_url or MCP_URL

    mcp_format, available_tools, detect_logs = detect_mcp_format(url, auth_token=auth_token)
    logs.extend(detect_logs)



    if mcp_format == MCPFormat.STDIO:
        return call_mcp_stdio(tool, city, server_config, timeout=15, auth_token=auth_token)

    if mcp_format == MCPFormat.STREAMABLE_HTTP:
        return call_mcp_streamable_http(url, tool, city, server_config, timeout=15, auth_token=auth_token)

    payload = build_mcp_payload(tool, city, mcp_format, available_tools, server_config)
    logs.append(f"📤 Payload ({mcp_format}): {json.dumps(payload, indent=2)}")

    try:
        log_msg = f"🔄 Calling MCP [{mcp_format}]: {tool} → {city} @ {url}"
        logs.append(log_msg)
        print(f"{Color.CYAN}{log_msg}{Color.END}")

        headers = {"Content-Type": "application/json"}
        if auth_token:
            masked = auth_token[:8] + "..." if len(auth_token) > 12 else "***"
            logs.append(f"🔐 [AUTH] Attaching Bearer token: {masked}")
            headers["Authorization"] = f"Bearer {auth_token}"
        res = requests.post(url, json=payload, headers=headers, timeout=15)

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

        # Handle echo servers (return payload back) - special case for testing
        is_echo = False
        if isinstance(raw_data, dict):
            # Check if response echoes our request (various patterns)
            if raw_data == payload:
                is_echo = True
            elif raw_data.get("tool") == payload.get("tool"):
                is_echo = True
            elif raw_data.get("jsonrpc") == "2.0" and "error" in raw_data:
                # Some test servers return errors for unknown tools - treat as echo
                err_msg = str(raw_data.get("error", "")).lower()
                if "tool" in err_msg or "method" in err_msg or "not found" in err_msg:
                    is_echo = True
                    logs.append("📢 Test server returned tool error - treating as echo/mock")
            elif "content" in raw_data and "isError" in raw_data:
                # JSON-RPC tool result format from some test servers
                is_echo = True
                logs.append("📢 Test server returned JSON-RPC result - treating as echo/mock")

        if is_echo:
            logs.append("⚠️ Echo server detected - no real data available")
            return {"error": "No data found", "logs": logs}

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


# ==============================
# 🧠 LLM-FIRST ORCHESTRATION
# ==============================

def llm_decide_needs_mcp(user_query):
    """
    Ask LLM to analyze the user query and decide if MCP data is needed.
    Returns: dict with keys: needs_mcp, cities, tools, reasoning, is_compare, is_general_chat
    """
    prompt = f"""You are GeoBot's intent analyzer. Analyze this user query and decide what the user needs.

USER QUERY: "{user_query}"

INSTRUCTIONS:
1. Determine if the user is asking about weather, AQI, time, holidays, or location data for a specific city.
2. If yes, extract the city name(s) and determine which tool(s) are needed.
3. If the user is comparing multiple cities, set is_compare=true.
4. If the user is asking a general question (not about any city data), set is_general_chat=true.
5. Return your analysis in this exact format:

MCP_NEEDED: true or false
CITIES: comma-separated list of city names (or "none")
TOOLS: comma-separated list of tools (getFullInsights, getWeatherOnly, getAQI, getTimeOnly, getCoordinatesOnly, getTodaySpecial) (or "none")
IS_COMPARE: true or false
IS_GENERAL_CHAT: true or false
REASONING: brief explanation of your decision

EXAMPLES:
- "Weather in Tokyo" → MCP_NEEDED: true, CITIES: Tokyo, TOOLS: getFullInsights, IS_COMPARE: false, IS_GENERAL_CHAT: false
- "What is AQI?" → MCP_NEEDED: false, CITIES: none, TOOLS: none, IS_COMPARE: false, IS_GENERAL_CHAT: true
- "Compare Delhi and Mumbai" → MCP_NEEDED: true, CITIES: Delhi,Mumbai, TOOLS: getFullInsights, IS_COMPARE: true, IS_GENERAL_CHAT: false
- "Hello" → MCP_NEEDED: false, CITIES: none, TOOLS: none, IS_COMPARE: false, IS_GENERAL_CHAT: true
- "Time in London" → MCP_NEEDED: true, CITIES: London, TOOLS: getTimeOnly, IS_COMPARE: false, IS_GENERAL_CHAT: false

Now analyze:
"""

    try:
        response = generate_llm_text(prompt)
        return parse_llm_decision(response, user_query)
    except Exception as e:
        print(f"{Color.RED}❌ LLM decision failed: {e}{Color.END}")
        # Fallback: use old extract_cities logic
        cities = extract_cities(user_query)
        return {
            "needs_mcp": len(cities) > 0,
            "cities": cities,
            "tools": ["getFullInsights"],
            "reasoning": "Fallback: extracted cities using regex",
            "is_compare": len(cities) > 1,
            "is_general_chat": len(cities) == 0
        }


def parse_llm_decision(response_text, original_query):
    """
    Parse the LLM's decision response into a structured dict.
    """
    result = {
        "needs_mcp": False,
        "cities": [],
        "tools": ["getFullInsights"],
        "reasoning": "",
        "is_compare": False,
        "is_general_chat": False
    }

    lines = response_text.strip().split('\n')
    for line in lines:
        line = line.strip()
        if line.startswith('MCP_NEEDED:'):
            val = line.split(':', 1)[1].strip().lower()
            result["needs_mcp"] = val in ['true', 'yes', '1']
        elif line.startswith('CITIES:'):
            val = line.split(':', 1)[1].strip()
            if val.lower() not in ['none', '', 'n/a']:
                result["cities"] = [c.strip() for c in val.split(',') if c.strip()]
        elif line.startswith('TOOLS:'):
            val = line.split(':', 1)[1].strip()
            if val.lower() not in ['none', '', 'n/a']:
                result["tools"] = [t.strip() for t in val.split(',') if t.strip()]
        elif line.startswith('IS_COMPARE:'):
            val = line.split(':', 1)[1].strip().lower()
            result["is_compare"] = val in ['true', 'yes', '1']
        elif line.startswith('IS_GENERAL_CHAT:'):
            val = line.split(':', 1)[1].strip().lower()
            result["is_general_chat"] = val in ['true', 'yes', '1']
        elif line.startswith('REASONING:'):
            result["reasoning"] = line.split(':', 1)[1].strip()

    # Fallback: if LLM didn't extract cities but we can find them
    if result["needs_mcp"] and not result["cities"]:
        fallback_cities = extract_cities(original_query)
        if fallback_cities:
            result["cities"] = fallback_cities

    return result


def llm_generate_with_data(user_query, mcp_data, reasoning=""):
    """
    Ask LLM to generate a final response using MCP data.
    """
    data_json = json.dumps(mcp_data, indent=2) if isinstance(mcp_data, (list, dict)) else str(mcp_data)

    prompt = f"""You are GeoBot, a friendly location intelligence assistant.

The user asked: "{user_query}"

Here is the real-time data:
{data_json}

Your task:
- Answer the user's question directly using the data above.
- Describe conditions in plain, natural language.
- Mention temperature, weather, AQI, or any relevant metrics.
- Do NOT use bullet points, headers, or numbered lists.
- Write in plain flowing text. Max 100 words.
- No emojis unless the user used them.
- If data is limited, mention what you can and be honest about gaps.

Answer:"""

    return generate_llm_text(prompt)


def llm_generate_general(user_query):
    """
    Ask LLM to answer a general question (no MCP data needed).
    """
    return generate_general_response(user_query)