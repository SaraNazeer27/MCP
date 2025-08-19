import asyncio
import aiohttp
import json
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.stdio import stdio_server
from typing import Dict, List, Any

# Configuration (override with environment variables)
import os
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:3008")
# Optional base context path to prefix in front of OPENAPI_PATH, e.g. "/csi-api/empi-api/api"
CONTEXT_PATH = os.getenv("CONTEXT_PATH", "")
# When provided, this full URL will be used directly and overrides API_BASE_URL/CONTEXT_PATH/OPENAPI_PATH
OPENAPI_FULL_URL = os.getenv("OPENAPI_FULL_URL", "")
OPENAPI_PATH = os.getenv("OPENAPI_PATH", "/v3/api-docs")
SERVER_NAME = os.getenv("SERVER_NAME", "fast_mcp_server")
# Runtime-resolved context path (may be auto-detected). Defaults to CONTEXT_PATH
CONTEXT_PATH_RUNTIME = CONTEXT_PATH
# Resolved OpenAPI URL after successful fetch (for logging)
RESOLVED_OPENAPI_URL = ""

# Create MCP server
server = Server(SERVER_NAME)


async def make_api_request(method: str, endpoint: str, data: dict = None) -> dict:
    """Make HTTP request to the configured API base URL"""
    base = API_BASE_URL.rstrip('/')
    ctx = CONTEXT_PATH_RUNTIME.strip('/').strip()
    ep = endpoint.lstrip('/')
    if ctx and not endpoint.startswith(f"/{ctx}") and not endpoint.startswith(ctx):
        url = f"{base}/{ctx}/{ep}" if ep else f"{base}/{ctx}"
    else:
        url = f"{base}/{ep}" if ep else base

    try:
        async with aiohttp.ClientSession() as session:
            if method.upper() == "GET":
                async with session.get(url) as response:
                    if response.status == 404:
                        return {"error": f"Not found: {await response.text()}"}
                    response.raise_for_status()
                    if response.status == 204:
                        return {"status": "no_content", "status_code": 204}
                    # Try JSON, fallback to text
                    try:
                        return await response.json()
                    except Exception:
                        return {"text": await response.text(), "status_code": response.status}

            elif method.upper() == "POST":
                async with session.post(url, json=data) as response:
                    response.raise_for_status()
                    if response.status == 204:
                        return {"status": "no_content", "status_code": 204}
                    try:
                        return await response.json()
                    except Exception:
                        return {"text": await response.text(), "status_code": response.status}

            elif method.upper() == "PUT":
                async with session.put(url, json=data) as response:
                    if response.status == 404:
                        return {"error": f"Not found: {await response.text()}"}
                    response.raise_for_status()
                    if response.status == 204:
                        return {"status": "no_content", "status_code": 204}
                    try:
                        return await response.json()
                    except Exception:
                        return {"text": await response.text(), "status_code": response.status}

            elif method.upper() == "DELETE":
                async with session.delete(url) as response:
                    if response.status == 404:
                        return {"error": f"Not found: {await response.text()}"}
                    response.raise_for_status()
                    if response.status == 204:
                        return {"status": "no_content", "status_code": 204}
                    try:
                        return await response.json()
                    except Exception:
                        return {"text": await response.text(), "status_code": response.status}

    except aiohttp.ClientError as e:
        return {"error": f"API request failed: {str(e)}"}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


async def fetch_openapi_spec() -> dict:
    """Fetch the OpenAPI spec from the configured API server.
    Implements simple auto-discovery of common servlet context-paths if initial fetch fails.
    Also updates CONTEXT_PATH_RUNTIME when a different context is detected.
    """
    global CONTEXT_PATH_RUNTIME, RESOLVED_OPENAPI_URL

    base = API_BASE_URL.rstrip('/')
    path = OPENAPI_PATH.strip('/')

    # Prepare candidate URLs in priority order
    candidates = []
    if OPENAPI_FULL_URL.strip():
        candidates.append(OPENAPI_FULL_URL.strip())
    else:
        ctx_env = CONTEXT_PATH.strip('/').strip()
        # From env (if any)
        if ctx_env:
            candidates.append(f"{base}/{ctx_env}/{path}")
        # No context
        candidates.append(f"{base}/{path}")
        # Common Spring context
        candidates.append(f"{base}/api/{path}")
        # Previously seen EMPI context example
        candidates.append(f"{base}/csi-api/empi-api/api/{path}")

    last_error = None
    try:
        async with aiohttp.ClientSession() as session:
            for cand in candidates:
                try:
                    async with session.get(cand) as response:
                        if response.status == 404:
                            # Save error and continue trying next candidate
                            text = await response.text()
                            last_error = {"error": f"404 Not Found for OpenAPI at {cand}. Response body: {text[:200]}"}
                            continue
                        response.raise_for_status()
                        data = await response.json()
                        # Basic sanity check: ensure it's an OpenAPI doc
                        if isinstance(data, dict) and ("openapi" in data or ("swagger" in data and data.get("swagger").startswith("2"))):
                            RESOLVED_OPENAPI_URL = cand
                            # If we auto-detected a context and OPENAPI_FULL_URL wasn't forced, update runtime context
                            if not OPENAPI_FULL_URL.strip():
                                # Derive context between base and path
                                try:
                                    # cand is like base + optional "/ctx" + "/" + path
                                    suffix = "/" + path
                                    if cand.startswith(base) and cand.endswith(suffix):
                                        mid = cand[len(base): -len(suffix)]  # e.g., "/api" or ""
                                        CONTEXT_PATH_RUNTIME = mid.strip("/")
                                except Exception:
                                    pass
                            return data
                        else:
                            # Unexpected but non-404; record and continue
                            last_error = {"error": f"Unexpected response at {cand}: not an OpenAPI JSON"}
                except aiohttp.ClientError as ce:
                    last_error = {"error": f"Client error fetching OpenAPI at {cand}: {str(ce)}"}
                except Exception as e:
                    last_error = {"error": f"Failed to fetch OpenAPI at {cand}: {str(e)}"}
    except Exception as outer:
        return {"error": f"Failed during OpenAPI discovery: {str(outer)}"}

    # If we get here, all candidates failed
    return last_error or {"error": "OpenAPI fetch failed for all candidates"}


def openapi_to_tools(openapi_spec: dict) -> List[Tool]:
    """Convert OpenAPI spec to a list of Tool objects."""
    tools = []
    paths = openapi_spec.get("paths", {})
    for path, methods in paths.items():
        for method, details in methods.items():
            if method.lower() not in {"get", "post", "put", "delete", "patch", "head", "options"}:
                continue  # skip non-operation keys like 'parameters'
            operation_id = details.get("operationId", f"{method}_{path}").replace("/", "_")
            summary = details.get("summary") or details.get("description") or f"{method.upper()} {path}"
            parameters = details.get("parameters", [])
            request_body = details.get("requestBody", {})
            input_schema = {"type": "object", "properties": {}, "required": []}
            # Parameters in path/query
            for param in parameters:
                pname = param["name"]
                pdesc = param.get("description", "")
                ptype = param.get("schema", {}).get("type", "string")
                input_schema["properties"][pname] = {"type": ptype, "description": pdesc}
                if param.get("required", False):
                    input_schema["required"].append(pname)
            # Parameters in body
            if request_body:
                content = request_body.get("content", {})
                app_json = content.get("application/json", {})
                schema = app_json.get("schema", {})
                if schema.get("type") == "object":
                    for pname, prop in schema.get("properties", {}).items():
                        input_schema["properties"][pname] = {"type": prop.get("type", "string"), "description": prop.get("description", "")}
                    input_schema["required"].extend(schema.get("required", []))
            # Remove duplicate required
            input_schema["required"] = list(set(input_schema["required"]))
            tools.append(Tool(
                name=operation_id,
                description=summary,
                inputSchema=input_schema
            ))
    return tools


# Global cache for OpenAPI spec and operation map
openapi_spec_cache = None
operation_map = None

async def ensure_openapi_loaded():
    global openapi_spec_cache, operation_map
    if openapi_spec_cache is not None and operation_map is not None:
        return
    openapi_spec_cache = await fetch_openapi_spec()
    if "error" in openapi_spec_cache:
        operation_map = {}
        return
    # Build operationId -> (method, path, details) map
    operation_map = {}
    for path, methods in openapi_spec_cache.get("paths", {}).items():
        for method, details in methods.items():
            if method.lower() not in {"get", "post", "put", "delete", "patch", "head", "options"}:
                continue
            operation_id = details.get("operationId", f"{method}_{path}").replace("/", "_")
            operation_map[operation_id] = (method.upper(), path, details)

# Utility to build request from operation details and arguments
def build_request_from_operation(method, path, details, arguments):
    # Substitute path parameters
    for param in details.get("parameters", []):
        if param.get("in") == "path":
            pname = param["name"]
            if pname not in arguments:
                raise ValueError(f"Missing required path parameter: {pname}")
            path = path.replace(f"{{{pname}}}", str(arguments[pname]))
    # Query params
    query_params = {}
    for param in details.get("parameters", []):
        if param.get("in") == "query" and param["name"] in arguments:
            query_params[param["name"]] = arguments[param["name"]]
    # Body
    body = None
    if details.get("requestBody"):
        content = details["requestBody"].get("content", {})
        if "application/json" in content:
            schema = content["application/json"].get("schema", {})
            if schema.get("type") == "object":
                body = {k: v for k, v in arguments.items() if k in schema.get("properties", {})}
    # Build endpoint with query string if needed
    if query_params:
        from urllib.parse import urlencode
        path = f"{path}?{urlencode(query_params)}"
    return method, path, body


@server.list_tools()
async def list_tools() -> List[Tool]:
    await ensure_openapi_loaded()
    if not openapi_spec_cache or not isinstance(openapi_spec_cache, dict) or "error" in openapi_spec_cache:
        err_msg = openapi_spec_cache["error"] if openapi_spec_cache and isinstance(openapi_spec_cache, dict) and "error" in openapi_spec_cache else "Unknown error"
        return [Tool(name="error", description=f"Failed to fetch OpenAPI: {err_msg}", inputSchema={"type": "object", "properties": {}})]
    tools = openapi_to_tools(openapi_spec_cache)
    # Add a maintenance tool to reload OpenAPI spec
    tools.append(Tool(name="reload_openapi_spec", description="Reload the OpenAPI specification from the configured server", inputSchema={"type": "object", "properties": {}}))
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    try:
        if name == "reload_openapi_spec":
            global openapi_spec_cache, operation_map
            openapi_spec_cache = None
            operation_map = None
            await ensure_openapi_loaded()
            if not openapi_spec_cache or "error" in openapi_spec_cache:
                return [TextContent(type="text", text=f"Failed to reload OpenAPI: {openapi_spec_cache['error'] if openapi_spec_cache else 'Unknown error'}")]
            return [TextContent(type="text", text="OpenAPI spec reloaded successfully.")]
        await ensure_openapi_loaded()
        if not operation_map or name not in operation_map:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        method, path, details = operation_map[name]
        try:
            http_method, endpoint, body = build_request_from_operation(method, path, details, arguments)
        except Exception as e:
            return [TextContent(type="text", text=f"Error building request: {str(e)}")]
        result = await make_api_request(http_method, endpoint, body)
        if "error" in result:
            return [TextContent(type="text", text=f"Error: {result['error']}")]
        pretty = json.dumps(result, indent=2, ensure_ascii=False)
        return [TextContent(type="text", text=f"Result for {name} (operationId):\n\n{pretty}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Error executing tool {name}: {str(e)}")]




async def main():
    # Test connection by fetching OpenAPI spec
    try:
        spec = await fetch_openapi_spec()
        display_url = RESOLVED_OPENAPI_URL or (OPENAPI_FULL_URL.strip() or f"{API_BASE_URL.rstrip('/')}/{(CONTEXT_PATH.strip('/').strip() + '/' if CONTEXT_PATH.strip('/') else '')}{OPENAPI_PATH.strip('/')}" )
        if isinstance(spec, dict) and ("openapi" in spec or ("swagger" in spec)):
            print(f"✅ Connected. OpenAPI loaded from {display_url}")
        else:
            print(f"⚠️ Received unexpected OpenAPI response from {display_url}: {str(spec)[:200]}")
    except Exception as e:
        openapi_url = OPENAPI_FULL_URL.strip() or f"{API_BASE_URL.rstrip('/')}/{OPENAPI_PATH.strip('/')}"
        print(f"❌ Failed to fetch OpenAPI from {openapi_url}: {e}")
        print("Make sure your API server is running and the OpenAPI path is correct. If your service has a servlet context-path (e.g., /csi-api/empi-api/api), either set CONTEXT_PATH or provide OPENAPI_FULL_URL.")
        return

    # Start MCP server
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())