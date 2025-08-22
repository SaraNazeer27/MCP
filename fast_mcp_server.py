import asyncio
import aiohttp
import json
from mcp.server import Server
from mcp.types import Tool, TextContent
from mcp.server.stdio import stdio_server
from typing import Dict, List, Any, Optional

# Configuration (override with environment variables)
import os
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:3008")
# Optional base context path to prefix in front of OPENAPI_PATH, e.g. "/csi-api/empi-api/api"
CONTEXT_PATH = os.getenv("CONTEXT_PATH", "")
# When provided, this full URL will be used directly and overrides API_BASE_URL/CONTEXT_PATH/OPENAPI_PATH
OPENAPI_FULL_URL = os.getenv("OPENAPI_FULL_URL", "")
OPENAPI_PATH = os.getenv("OPENAPI_PATH", "/v3/api-docs")
SERVER_NAME = os.getenv("SERVER_NAME", "fast_mcp_server")
# Optional default group header value for downstream APIs that require x-group
# Do not hardcode a numeric default; use environment only, else leave unset to force user input via tools
DEFAULT_X_GROUP = (os.getenv("DEFAULT_X_GROUP") or os.getenv("X_GROUP") or "").strip() or None
# Optional default hospital header value for downstream APIs that require x-hospital
# Do not hardcode a numeric default; use environment only, else leave unset to force user input via tools
DEFAULT_X_HOSPITAL = (os.getenv("DEFAULT_X_HOSPITAL") or os.getenv("X_HOSPITAL") or "").strip() or None
# Runtime-resolved context path (may be auto-detected). Defaults to CONTEXT_PATH
CONTEXT_PATH_RUNTIME = CONTEXT_PATH
# Resolved OpenAPI URL after successful fetch (for logging)
RESOLVED_OPENAPI_URL = ""

# Auto test configuration
AUTO_RUN_EMPI_TESTS = (os.getenv("AUTO_RUN_EMPI_TESTS", "1").strip().lower() in {"1", "true", "yes", "on"})
try:
    AUTO_TEST_CONCURRENCY = int(os.getenv("AUTO_TEST_CONCURRENCY", "5"))
except Exception:
    AUTO_TEST_CONCURRENCY = 5
AUTO_TEST_FILTER = (os.getenv("AUTO_TEST_FILTER", "").strip() or None)
AUTO_TEST_REPORT = os.getenv("AUTO_TEST_REPORT", os.path.join(os.getcwd(), "empi_test_report.json"))

# Create MCP server
server = Server(SERVER_NAME)

# Runtime user-provided header overrides (set through MCP tools)
USER_X_GROUP: Optional[str] = None
USER_X_HOSPITAL: Optional[str] = None


async def make_api_request(method: str, endpoint: str, data: dict = None, headers: Optional[Dict[str, str]] = None) -> dict:
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
                async with session.get(url, headers=headers or {}) as response:
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
                async with session.post(url, json=data, headers=headers or {}) as response:
                    response.raise_for_status()
                    if response.status == 204:
                        return {"status": "no_content", "status_code": 204}
                    try:
                        return await response.json()
                    except Exception:
                        return {"text": await response.text(), "status_code": response.status}

            elif method.upper() == "PUT":
                async with session.put(url, json=data, headers=headers or {}) as response:
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
                async with session.delete(url, headers=headers or {}) as response:
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
            # Parameters in path/query (skip headers, they can be auto-injected)
            for param in parameters:
                if param.get("in") not in {"path", "query"}:
                    continue
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
    from urllib.parse import quote as _urlquote
    for param in details.get("parameters", []):
        if param.get("in") == "path":
            pname = param["name"]
            if pname not in arguments:
                raise ValueError(f"Missing required path parameter: {pname}")
            encoded_val = _urlquote(str(arguments[pname]), safe="")
            path = path.replace(f"{{{pname}}}", encoded_val)
    # Query params
    query_params = {}
    # Headers
    headers = {}
    # Prepare case-insensitive lookup for provided args (hyphen vs underscore)
    arg_keys = {k.lower(): k for k in arguments.keys()}
    for param in details.get("parameters", []):
        loc = param.get("in")
        pname = param["name"]
        if loc == "query" and pname in arguments:
            query_params[pname] = arguments[pname]
        elif loc == "header":
            # Accept both 'x-group' and 'x_group' forms
            lname = pname.lower()
            provided_key = None
            if pname in arguments:
                provided_key = pname
            elif lname in arg_keys:
                provided_key = arg_keys[lname]
            elif lname.replace("-", "_") in arg_keys:
                provided_key = arg_keys[lname.replace("-", "_")]
            if provided_key is not None:
                headers[pname] = str(arguments[provided_key])
            else:
                # Inject from user override first, then default env values
                if lname == "x-group":
                    if USER_X_GROUP:
                        headers[pname] = USER_X_GROUP
                    elif DEFAULT_X_GROUP:
                        headers[pname] = DEFAULT_X_GROUP
                if lname == "x-hospital":
                    if USER_X_HOSPITAL:
                        headers[pname] = USER_X_HOSPITAL
                    elif DEFAULT_X_HOSPITAL:
                        headers[pname] = DEFAULT_X_HOSPITAL
                # Otherwise, leave it out; backend may apply its own default
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
    return method, path, body, headers


@server.list_tools()
async def list_tools() -> List[Tool]:
    await ensure_openapi_loaded()
    if not openapi_spec_cache or not isinstance(openapi_spec_cache, dict) or "error" in openapi_spec_cache:
        err_msg = openapi_spec_cache["error"] if openapi_spec_cache and isinstance(openapi_spec_cache, dict) and "error" in openapi_spec_cache else "Unknown error"
        return [Tool(name="error", description=f"Failed to fetch OpenAPI: {err_msg}", inputSchema={"type": "object", "properties": {}})]
    tools = openapi_to_tools(openapi_spec_cache)
    # Add maintenance and configuration tools
    tools.append(Tool(
        name="reload_openapi_spec",
        description="Reload the OpenAPI specification from the configured server",
        inputSchema={"type": "object", "properties": {}}
    ))
    tools.append(Tool(
        name="set_x_group",
        description="Set the x-group header value to use for downstream API requests (overrides environment default).",
        inputSchema={
            "type": "object",
            "properties": {
                "x_group": {"type": "string", "description": "The x-group value. Accepts digits or string."}
            },
            "required": ["x_group"]
        }
    ))
    tools.append(Tool(
        name="set_x_hospital",
        description="Set the x-hospital header value to use for downstream API requests (overrides environment default).",
        inputSchema={
            "type": "object",
            "properties": {
                "x_hospital": {"type": "string", "description": "The x-hospital value. Accepts digits or string."}
            },
            "required": ["x_hospital"]
        }
    ))
    tools.append(Tool(
        name="show_header_context",
        description="Show current x-group/x-hospital values in use (user override vs defaults).",
        inputSchema={"type": "object", "properties": {}}
    ))
    tools.append(Tool(
        name="clear_header_context",
        description="Clear user-provided x-group and x-hospital overrides (revert to defaults).",
        inputSchema={"type": "object", "properties": {}}
    ))
    tools.append(Tool(
        name="run_empi_tests",
        description=(
            "Run the auto-generated integration tests against the current OpenAPI service. "
            "Options: set concurrency, filter operations, override x-group/x-hospital, and choose a report path."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "concurrency": {"type": "integer", "description": "Number of concurrent requests (default 5)"},
                "filter": {"type": "string", "description": "Filter operations by text (operationId/summary)"},
                "report": {"type": "string", "description": "Path to save JSON report (default empi_test_report.json)"},
                "x_group": {"type": "string", "description": "x-group header override"},
                "x_hospital": {"type": "string", "description": "x-hospital header override"}
            }
        }
    ))
    return tools


async def _execute_empi_tests(concurrency: Optional[int] = None,
                              filter_text: Optional[str] = None,
                              report_path: Optional[str] = None,
                              x_group: Optional[str] = None,
                              x_hospital: Optional[str] = None,
                              background_label: Optional[str] = None) -> str:
    """Execute the integration test script as a subprocess and return combined output text.
    Uses USER_X_GROUP/USER_X_HOSPITAL overrides if provided, otherwise defaults.
    """
    import sys
    from asyncio.subprocess import PIPE, create_subprocess_exec

    script_path = os.path.join(os.path.dirname(__file__), "test_empi_endpoints.py")
    if not os.path.exists(script_path):
        return f"Test script not found at {script_path}"

    conc_val = str(concurrency if concurrency is not None else AUTO_TEST_CONCURRENCY)
    report_val = report_path or AUTO_TEST_REPORT

    cmd = [sys.executable or "python", script_path, "--concurrency", conc_val, "--report", report_val]
    if filter_text:
        cmd += ["--filter", str(filter_text)]

    # Prefer user overrides if already set via tools; otherwise use provided args or env defaults
    xg = (x_group if x_group is not None else (USER_X_GROUP or DEFAULT_X_GROUP))
    xh = (x_hospital if x_hospital is not None else (USER_X_HOSPITAL or DEFAULT_X_HOSPITAL))
    if xg:
        cmd += ["--x-group", str(xg)]
    if xh:
        cmd += ["--x-hospital", str(xh)]

    try:
        proc = await create_subprocess_exec(*cmd, stdout=PIPE, stderr=PIPE, env=os.environ.copy())
        stdout_b, stderr_b = await proc.communicate()
        out = stdout_b.decode("utf-8", errors="replace")
        err = stderr_b.decode("utf-8", errors="replace")
        header_lines = []
        if background_label:
            header_lines.append(f"[auto:{background_label}] EMPI tests started...")
        header_lines.append(f"Command: {' '.join(cmd)}")
        header_lines.append(f"Exit code: {proc.returncode}")
        header_lines.append(f"Report: {report_val}")
        header = "\n".join(header_lines) + "\n\n"
        text = header + out
        if err.strip():
            text += "\n[stderr]\n" + err
        if len(text) > 12000:
            text = text[:12000] + "\n... (truncated)"
        return text
    except Exception as e:
        return f"Failed to run tests: {str(e)}"


async def _auto_run_and_print(label: str):
    """Run tests in background and print the result to stdout when done."""
    try:
        print(f"[auto:{label}] Launching integration tests (this runs in background)...")
        # Apply AUTO_TEST_FILTER if provided via environment
        text = await _execute_empi_tests(filter_text=AUTO_TEST_FILTER, background_label=label)
        print(text)
    except Exception as e:
        print(f"[auto:{label}] Failed to run auto tests: {e}")


@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
    try:
        global USER_X_GROUP, USER_X_HOSPITAL, openapi_spec_cache, operation_map
        # Config tools that do not require OpenAPI
        if name == "set_x_group":
            # Accept x_group or x-group
            raw = arguments.get("x_group")
            if raw is None:
                raw = arguments.get("x-group")
            if raw is None:
                return [TextContent(type="text", text="Missing required argument: x_group")]
            value = str(raw).strip()
            if not value:
                return [TextContent(type="text", text="x_group cannot be empty.")]
            USER_X_GROUP = value
            return [TextContent(type="text", text=f"x-group set to: {USER_X_GROUP}")]
        if name == "set_x_hospital":
            raw = arguments.get("x_hospital")
            if raw is None:
                raw = arguments.get("x-hospital")
            if raw is None:
                return [TextContent(type="text", text="Missing required argument: x_hospital")]
            value = str(raw).strip()
            if not value:
                return [TextContent(type="text", text="x_hospital cannot be empty.")]
            USER_X_HOSPITAL = value
            return [TextContent(type="text", text=f"x-hospital set to: {USER_X_HOSPITAL}")]
        if name == "show_header_context":
            resolved_group = USER_X_GROUP or DEFAULT_X_GROUP or "<unset>"
            resolved_hospital = USER_X_HOSPITAL or DEFAULT_X_HOSPITAL or "<unset>"
            msg = (
                "Header context:\n"
                f"  x-group: current={'user:' + USER_X_GROUP if USER_X_GROUP else 'default:' + (DEFAULT_X_GROUP or '<unset>')} (resolved={resolved_group})\n"
                f"  x-hospital: current={'user:' + USER_X_HOSPITAL if USER_X_HOSPITAL else 'default:' + (DEFAULT_X_HOSPITAL or '<unset>')} (resolved={resolved_hospital})\n"
            )
            return [TextContent(type="text", text=msg)]
        if name == "clear_header_context":
            USER_X_GROUP = None
            USER_X_HOSPITAL = None
            return [TextContent(type="text", text="Cleared user-provided x-group and x-hospital. Defaults will be used.")]
        if name == "reload_openapi_spec":
            openapi_spec_cache = None
            operation_map = None
            await ensure_openapi_loaded()
            if not openapi_spec_cache or "error" in openapi_spec_cache:
                return [TextContent(type="text", text=f"Failed to reload OpenAPI: {openapi_spec_cache['error'] if openapi_spec_cache else 'Unknown error'}")]
            # Optionally auto-run tests after reload
            if AUTO_RUN_EMPI_TESTS:
                asyncio.create_task(_auto_run_and_print("reload"))
                return [TextContent(type="text", text="OpenAPI spec reloaded successfully. Auto-running integration tests in background...")]
            return [TextContent(type="text", text="OpenAPI spec reloaded successfully.")]
        if name == "run_empi_tests":
            # Run the standalone test script as a subprocess and return its output
            text = await _execute_empi_tests(
                concurrency=arguments.get("concurrency"),
                filter_text=arguments.get("filter"),
                report_path=arguments.get("report"),
                x_group=arguments.get("x_group") or arguments.get("x-group"),
                x_hospital=arguments.get("x_hospital") or arguments.get("x-hospital")
            )
            return [TextContent(type="text", text=text)]
        # Operation tools (require OpenAPI)
        await ensure_openapi_loaded()
        if not operation_map or name not in operation_map:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        method, path, details = operation_map[name]
        try:
            http_method, endpoint, body, headers = build_request_from_operation(method, path, details, arguments)
        except Exception as e:
            return [TextContent(type="text", text=f"Error building request: {str(e)}")]
        # Ensure x-group header is applied if not already set by operation headers
        if ("x-group" not in headers and "X-Group" not in headers):
            if USER_X_GROUP:
                headers["x-group"] = USER_X_GROUP
            elif DEFAULT_X_GROUP:
                headers["x-group"] = DEFAULT_X_GROUP
        # Ensure x-hospital header is applied if not already set by operation headers
        if ("x-hospital" not in headers and "X-Hospital" not in headers):
            if USER_X_HOSPITAL:
                headers["x-hospital"] = USER_X_HOSPITAL
            elif DEFAULT_X_HOSPITAL:
                headers["x-hospital"] = DEFAULT_X_HOSPITAL
        # If still missing, ask the agent to collect them from the user via the provided tools
        missing_prompts = []
        if ("x-group" not in headers and "X-Group" not in headers):
            missing_prompts.append("x-group (call set_x_group with { x_group: <value> })")
        if ("x-hospital" not in headers and "X-Hospital" not in headers):
            missing_prompts.append("x-hospital (call set_x_hospital with { x_hospital: <value> })")
        if missing_prompts:
            return [TextContent(type="text", text=(
                "Missing required header(s): " + ", ".join(missing_prompts) +
                "\nPlease ask the user for these values and set them using the corresponding tools, then re-run the operation."
            ))]
        result = await make_api_request(http_method, endpoint, body, headers)
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
            # Auto-run integration tests on startup if enabled
            if AUTO_RUN_EMPI_TESTS:
                asyncio.create_task(_auto_run_and_print("startup"))
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