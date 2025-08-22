MCP server for any OpenAPI REST service (including EMPI Java/Spring)

This repo contains a Model Context Protocol (MCP) server that dynamically exposes your REST endpoints as MCP tools by reading the service's OpenAPI document.

How it works
- The server fetches the OpenAPI spec from your service (e.g., Spring Boot via springdoc at /v3/api-docs).
- It converts each operationId into an MCP tool with an appropriate input schema from parameters and request bodies.
- When a tool is invoked, it builds the HTTP request (path, query, and JSON body) and calls your service.

Configure for your EMPI Java service
Set the following environment variables when launching the MCP server:
- API_BASE_URL: Base URL of your EMPI service. Example: http://localhost:8080
- OPENAPI_PATH: Path to the OpenAPI JSON. Common Spring path: /v3/api-docs
- CONTEXT_PATH: Optional. If your service runs under a servlet context-path, set it here (e.g., /csi-api/empi-api/api). It will be prefixed before OPENAPI_PATH when fetching the spec.
- OPENAPI_FULL_URL: Optional. If set, this full URL will be used directly to fetch the OpenAPI spec (overrides the previous two). Example: http://localhost:3008/csi-api/empi-api/api/v3/api-docs
- SERVER_NAME: Optional. Name to display for the MCP server. Default: fast_mcp_server
- DEFAULT_X_GROUP or X_GROUP: Optional. If your EMPI API requires the x-group header, set a default here. The MCP server will inject this header on every request unless explicitly provided in a tool call.
- DEFAULT_X_HOSPITAL or X_HOSPITAL: Optional. If your API requires the x-hospital header, set a default here. The MCP server will inject this header on every request unless explicitly provided in a tool call.

Example mcp.json
{
  "servers": {
    "empi-mcp": {
      "command": "python",
      "args": ["fast_mcp_server.py"],
      "cwd": "/home/zeinab/Documents/CSI_main/Tasks/MCP/MCP",
      "env": {
        "API_BASE_URL": "http://localhost:80",
        "CONTEXT_PATH": "/csi-api/empi-api/api",
        "OPENAPI_PATH": "/v3/api-docs",
        "SERVER_NAME": "empi-openapi-mcp"
      }
    }
  }
}

Running against the included demo FastAPI service
- If you use the included FastAPI demo (fast_api_server.py) which runs on http://127.0.0.1:8000, set:
  - API_BASE_URL=http://127.0.0.1:8000
  - OPENAPI_PATH=/openapi.json
  - CONTEXT_PATH is not needed for this demo (leave unset)

Maintenance tool
- The MCP server also exposes a maintenance tool: reload_openapi_spec, which re-fetches the OpenAPI and rebuilds the tool list.

Installation
- Python 3.9+
- Create and use a virtual environment (recommended, avoids PEP 668 externally-managed-environment errors):
  1. python3 -m venv .venv
  2. source .venv/bin/activate
  3. pip install -r requirements.txt

Troubleshooting PEP 668 (externally managed environment)
- If you see an error like "externally-managed-environment" when installing packages system-wide, you are on a distro-managed Python. Use a virtual environment as shown above.
- If you intentionally want to install into the system Python (not recommended), you can run:
  - pip install --break-system-packages -r requirements.txt

Running
- After activating the venv:
  - python fast_mcp_server.py
- To run the demo FastAPI server (optional):
  - python fast_api_server.py

Dependencies
- Managed via requirements.txt (includes: mcp, aiohttp). Ensure your virtual environment is active when running scripts.

Notes
- Ensure your EMPI service exposes OpenAPI JSON (with springdoc-openapi: GET /v3/api-docs).
- Auto-discovery: If the first attempt to fetch OpenAPI fails, the server will try common context-paths like /api and /csi-api/empi-api/api. When a working URL is found, it will use that context for subsequent API calls.
- If your Swagger UI is at a URL like http://localhost:3008/api/swagger-ui/index.html, your OpenAPI JSON is likely at http://localhost:3008/api/v3/api-docs (context-path=/api). You can either set CONTEXT_PATH=/api or rely on auto-discovery.
- Endpoints requiring authorization or tenancy headers: This server now supports injecting required headers like x-group and x-hospital. Set DEFAULT_X_GROUP (or X_GROUP) and/or DEFAULT_X_HOSPITAL (or X_HOSPITAL) in env to provide defaults. You can also pass explicit headers in a tool call argument as either "x-group"/"x_group" or "x-hospital"/"x_hospital" to override the defaults for that call.
