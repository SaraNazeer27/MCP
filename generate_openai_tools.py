import requests
import json

OPENAPI_URL = "http://localhost:8000/openapi.json"
OUTPUT_FILE = "openai.json"

def fetch_openapi_schema(url):
    response = requests.get(url)
    response.raise_for_status()
    return response.json()

def extract_tools_from_openapi(openapi_schema):
    tools = []
    paths = openapi_schema.get("paths", {})
    for path, methods in paths.items():
        for method, details in methods.items():
            tool = {
                "name": details.get("operationId", f"{method}_{path}"),
                "description": details.get("summary", details.get("description", "")),
                "method": method.upper(),
                "path": path,
                "parameters": details.get("parameters", []),
                "requestBody": details.get("requestBody", {})
            }
            tools.append(tool)
    return tools

def main():
    openapi_schema = fetch_openapi_schema(OPENAPI_URL)
    tools = extract_tools_from_openapi(openapi_schema)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(tools, f, indent=2)
    print(f"Extracted {len(tools)} tools to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()

