import asyncio
import aiohttp
import argparse
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

# Environment-configurable settings (aligned with fast_mcp_server.py)
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:3008").rstrip("/")
CONTEXT_PATH = os.getenv("CONTEXT_PATH", "").strip()
OPENAPI_PATH = os.getenv("OPENAPI_PATH", "/v3/api-docs").strip()
OPENAPI_FULL_URL = os.getenv("OPENAPI_FULL_URL", "").strip()
DEFAULT_X_GROUP = (os.getenv("DEFAULT_X_GROUP") or os.getenv("X_GROUP") or "").strip() or None
DEFAULT_X_HOSPITAL = (os.getenv("DEFAULT_X_HOSPITAL") or os.getenv("X_HOSPITAL") or "").strip() or None

# Will be updated at runtime if auto-discovery finds a different context
CONTEXT_PATH_RUNTIME = CONTEXT_PATH
RESOLVED_OPENAPI_URL = ""


def sanitize_operation_id(op_id: str) -> str:
    # Create a filesystem/console-friendly id
    s = re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", op_id)
    return s[:200]


async def fetch_openapi_spec() -> Dict[str, Any]:
    global CONTEXT_PATH_RUNTIME, RESOLVED_OPENAPI_URL

    base = API_BASE_URL.rstrip('/')
    path = OPENAPI_PATH.strip('/')

    candidates: List[str] = []
    if OPENAPI_FULL_URL:
        candidates.append(OPENAPI_FULL_URL)
    else:
        ctx_env = CONTEXT_PATH.strip('/').strip()
        if ctx_env:
            candidates.append(f"{base}/{ctx_env}/{path}")
        candidates.append(f"{base}/{path}")
        candidates.append(f"{base}/api/{path}")
        candidates.append(f"{base}/csi-api/empi-api/api/{path}")

    last_error: Optional[Dict[str, Any]] = None
    try:
        async with aiohttp.ClientSession() as session:
            for cand in candidates:
                try:
                    async with session.get(cand) as resp:
                        if resp.status == 404:
                            txt = await resp.text()
                            last_error = {"error": f"404 Not Found at {cand}: {txt[:200]}"}
                            continue
                        resp.raise_for_status()
                        data = await resp.json()
                        if isinstance(data, dict) and ("openapi" in data or ("swagger" in data and str(data.get("swagger")).startswith("2"))):
                            RESOLVED_OPENAPI_URL = cand
                            if not OPENAPI_FULL_URL:
                                try:
                                    suffix = "/" + path
                                    if cand.startswith(base) and cand.endswith(suffix):
                                        mid = cand[len(base): -len(suffix)]
                                        CONTEXT_PATH_RUNTIME = mid.strip("/")
                                except Exception:
                                    pass
                            return data
                        last_error = {"error": f"Unexpected non-OpenAPI response at {cand}"}
                except aiohttp.ClientError as ce:
                    last_error = {"error": f"Client error getting OpenAPI at {cand}: {str(ce)}"}
                except Exception as e:
                    last_error = {"error": f"Error getting OpenAPI at {cand}: {str(e)}"}
    except Exception as outer:
        return {"error": f"OpenAPI discovery failed: {str(outer)}"}

    return last_error or {"error": "OpenAPI fetch failed"}


def synth_value(prop_schema: Dict[str, Any], name: str = "") -> Any:
    t = (prop_schema or {}).get("type")
    fmt = (prop_schema or {}).get("format")
    enum = (prop_schema or {}).get("enum")
    if enum:
        return enum[0]
    if t == "integer":
        # common ID hints
        if name.lower().endswith("id") or name.lower() in {"id", "book_id"}:
            return 1
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return True
    if t == "array":
        items = (prop_schema or {}).get("items", {})
        return [synth_value(items, name=name+"_item")]
    if t == "object":
        props = (prop_schema or {}).get("properties", {})
        return {k: synth_value(v, name=k) for k, v in props.items()}
    # string fallback
    if fmt == "date-time":
        return "2000-01-01T00:00:00Z"
    if fmt == "date":
        return "2000-01-01"
    if name:
        return f"sample_{name}"
    return "sample"


def build_request_from_operation(method: str, path: str, details: Dict[str, Any], base_args: Dict[str, Any]) -> Tuple[str, str, Optional[Dict[str, Any]], Dict[str, str]]:
    # Substitute path params
    from urllib.parse import quote as _urlquote
    for param in details.get("parameters", []):
        if param.get("in") == "path":
            pname = param["name"]
            if pname not in base_args:
                raise ValueError(f"Missing required path parameter: {pname}")
            encoded = _urlquote(str(base_args[pname]), safe="")
            path = path.replace(f"{{{pname}}}", encoded)

    # Query params
    query_params: Dict[str, Any] = {}
    headers: Dict[str, str] = {}

    # Allow case variations for provided args
    arg_keys = {k.lower(): k for k in base_args.keys()}

    for param in details.get("parameters", []):
        loc = param.get("in")
        pname = param["name"]
        if loc == "query" and pname in base_args:
            query_params[pname] = base_args[pname]
        elif loc == "header":
            lname = pname.lower()
            provided_key = None
            if pname in base_args:
                provided_key = pname
            elif lname in arg_keys:
                provided_key = arg_keys[lname]
            elif lname.replace("-", "_") in arg_keys:
                provided_key = arg_keys[lname.replace("-", "_")]
            if provided_key is not None:
                headers[pname] = str(base_args[provided_key])

    body: Optional[Dict[str, Any]] = None
    if details.get("requestBody"):
        content = details["requestBody"].get("content", {})
        if "application/json" in content:
            schema = content["application/json"].get("schema", {})
            if schema.get("type") == "object":
                body = {k: v for k, v in base_args.items() if k in schema.get("properties", {})}

    # Apply query string
    if query_params:
        from urllib.parse import urlencode
        path = f"{path}?{urlencode(query_params)}"

    return method.upper(), path, body, headers


def synthesize_base_args(details: Dict[str, Any]) -> Dict[str, Any]:
    args: Dict[str, Any] = {}
    # parameters in path/query
    for param in details.get("parameters", []):
        if param.get("in") in {"path", "query"}:
            pname = param["name"]
            ptype = (param.get("schema") or {}).get("type", "string")
            args[pname] = synth_value({"type": ptype}, name=pname)
    # requestBody
    if details.get("requestBody"):
        content = details["requestBody"].get("content", {})
        app_json = content.get("application/json", {})
        schema = app_json.get("schema", {})
        if schema.get("type") == "object":
            for pname, prop in (schema.get("properties") or {}).items():
                args[pname] = synth_value(prop, name=pname)
    return args


async def run_single_test(session: aiohttp.ClientSession,
                          base: str,
                          ctx_runtime: str,
                          operation_id: str,
                          method: str,
                          path: str,
                          details: Dict[str, Any],
                          x_group: Optional[str],
                          x_hospital: Optional[str]) -> Dict[str, Any]:
    # Prepare base args
    base_args = synthesize_base_args(details)

    results: List[Dict[str, Any]] = []

    async def _execute(label: str,
                       omit_required: Optional[str] = None,
                       tweak_id_to_invalid: bool = False) -> Dict[str, Any]:
        args = dict(base_args)
        if omit_required and omit_required in args:
            args.pop(omit_required, None)
        # If there is a numeric path ID, optionally make it unlikely/invalid
        if tweak_id_to_invalid:
            for param in details.get("parameters", []):
                if param.get("in") == "path":
                    pname = param["name"]
                    schema_type = (param.get("schema") or {}).get("type")
                    if schema_type in {"integer", "number"} and pname in args:
                        args[pname] = 999999999
        try:
            http_method, endpoint, body, headers = build_request_from_operation(method, path, details, args)
        except Exception as e:
            return {
                "label": label,
                "status": "error",
                "error": f"build_request_error: {str(e)}"
            }

        # Compose full URL
        ep = endpoint.lstrip('/')
        if ctx_runtime and not endpoint.startswith(f"/{ctx_runtime}") and not endpoint.startswith(ctx_runtime):
            url = f"{base}/{ctx_runtime}/{ep}" if ep else f"{base}/{ctx_runtime}"
        else:
            url = f"{base}/{ep}" if ep else base

        # Ensure EMPI headers if applicable
        # Prefer explicit headers synthesized from parameters
        if x_group and "x-group" not in headers and "X-Group" not in headers:
            headers["x-group"] = x_group
        if x_hospital and "x-hospital" not in headers and "X-Hospital" not in headers:
            headers["x-hospital"] = x_hospital

        req_info = {"method": http_method, "url": url, "headers": headers, "body": body}

        try:
            if http_method == "GET":
                async with session.get(url, headers=headers) as resp:
                    status = resp.status
                    text = await resp.text()
            elif http_method == "POST":
                async with session.post(url, headers=headers, json=body) as resp:
                    status = resp.status
                    text = await resp.text()
            elif http_method == "PUT":
                async with session.put(url, headers=headers, json=body) as resp:
                    status = resp.status
                    text = await resp.text()
            elif http_method == "DELETE":
                async with session.delete(url, headers=headers) as resp:
                    status = resp.status
                    text = await resp.text()
            else:
                async with session.request(http_method, url, headers=headers, json=body) as resp:
                    status = resp.status
                    text = await resp.text()
        except Exception as e:
            return {
                "label": label,
                "status": "fail",
                "request": req_info,
                "reason": f"request_error: {str(e)}"
            }

        entry = {
            "label": label,
            "status_code": status,
            "ok": 200 <= status < 300,
            "request": req_info,
            "response_snippet": text[:1000]
        }

        return entry

    # Define scenarios
    scenarios: List[Tuple[str, Dict[str, Any]]] = []

    # 1) Happy path: send synthesized data
    scenarios.append(("happy_path", {}))

    # 2) Negative: omit a required field/param if any
    required_fields: List[str] = []
    for p in details.get("parameters", []):
        if p.get("required") and p.get("in") in {"path", "query"}:
            required_fields.append(p["name"])
    req_body = details.get("requestBody") or {}
    content = (req_body.get("content") or {}).get("application/json", {})
    schema = content.get("schema") or {}
    for f in schema.get("required", []) if schema.get("type") == "object" else []:
        required_fields.append(f)
    if required_fields:
        scenarios.append(("negative_missing_required", {"omit_required": required_fields[0]}))

    # 3) Negative: tweak numeric ID to an unlikely value to provoke 404
    has_numeric_path = any(((p.get("schema") or {}).get("type") in {"integer", "number"}) and p.get("in") == "path" for p in details.get("parameters", []))
    if has_numeric_path:
        scenarios.append(("negative_invalid_id", {"tweak_id_to_invalid": True}))

    async with aiohttp.ClientSession() as s2:
        # Note: prefer provided session but keep new for isolation if desired
        pass

    # Execute scenarios sequentially to keep causal clarity per operation
    for label, opts in scenarios:
        res = await _execute(label, omit_required=opts.get("omit_required"), tweak_id_to_invalid=opts.get("tweak_id_to_invalid", False))
        results.append(res)

    # Determine overall pass/fail policy:
    # - happy_path passes if 2xx
    # - negative_missing_required passes if received 400/422
    # - negative_invalid_id passes if received 404
    overall = "PASS"
    reasons: List[str] = []

    mapping_expect = {
        "happy_path": lambda r: bool(r.get("ok")),
        "negative_missing_required": lambda r: r.get("status_code") in {400, 422},
        "negative_invalid_id": lambda r: r.get("status_code") == 404,
    }

    by_label = {r.get("label"): r for r in results}
    for label, predicate in mapping_expect.items():
        r = by_label.get(label)
        if r is None:
            continue
        try:
            if not predicate(r):
                overall = "FAIL"
                # reason summarize
                reasons.append(f"{label} expected different status, got {r.get('status_code')}")
        except Exception:
            overall = "FAIL"
            reasons.append(f"{label} evaluation error")

    return {
        "operationId": operation_id,
        "method": method,
        "path": path,
        "scenarios": results,
        "overall": overall,
        "reason": "; ".join(reasons) if reasons else ""
    }


async def test_all_endpoints(openapi: Dict[str, Any], concurrency: int, 
                             x_group: Optional[str], x_hospital: Optional[str], 
                             filter_text: Optional[str]) -> Dict[str, Any]:
    paths = openapi.get("paths", {})

    # Build tasks list
    tasks: List[Tuple[str, str, str, Dict[str, Any]]] = []  # (operationId, method, path, details)
    for path, methods in paths.items():
        for method, details in methods.items():
            if method.lower() not in {"get", "post", "put", "delete", "patch", "head", "options"}:
                continue
            op_id = details.get("operationId", f"{method}_{path}")
            if filter_text:
                target = (op_id + " " + (details.get("summary") or details.get("description") or ""))
                if filter_text.lower() not in target.lower():
                    continue
            tasks.append((op_id, method.upper(), path, details))

    results: List[Dict[str, Any]] = []

    sem = asyncio.Semaphore(concurrency)

    async with aiohttp.ClientSession() as session:
        async def worker(op_id: str, method: str, path: str, details: Dict[str, Any]):
            async with sem:
                try:
                    res = await run_single_test(session, API_BASE_URL, CONTEXT_PATH_RUNTIME, sanitize_operation_id(op_id), method, path, details, x_group, x_hospital)
                    results.append(res)
                except Exception as e:
                    results.append({
                        "operationId": op_id,
                        "method": method,
                        "path": path,
                        "overall": "FAIL",
                        "reason": f"runner_exception: {str(e)}",
                        "scenarios": []
                    })

        await asyncio.gather(*(worker(op_id, method, path, details) for op_id, method, path, details in tasks))

    # Aggregate
    passed = sum(1 for r in results if r.get("overall") == "PASS")
    failed = len(results) - passed
    summary = {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "openapi_url": RESOLVED_OPENAPI_URL or (OPENAPI_FULL_URL or f"{API_BASE_URL}/{(CONTEXT_PATH_RUNTIME.strip('/') + '/' if CONTEXT_PATH_RUNTIME.strip('/') else '')}{OPENAPI_PATH.strip('/')}")
    }
    return {"summary": summary, "results": sorted(results, key=lambda r: r.get("operationId", ""))}


def print_human_report(report: Dict[str, Any]) -> None:
    summary = report.get("summary", {})
    print("\n=== EMPI Integration Test Report ===")
    print(f"OpenAPI: {summary.get('openapi_url')}")
    print(f"Total: {summary.get('total')} | Passed: {summary.get('passed')} | Failed: {summary.get('failed')}")
    print("-----------------------------------")

    for r in report.get("results", []):
        status = r.get("overall")
        op = r.get("operationId")
        method = r.get("method")
        path = r.get("path")
        reason = r.get("reason")
        mark = "PASS" if status == "PASS" else "FAIL"
        print(f"[{mark}] {method} {path}  op={op}")
        if status != "PASS" and reason:
            print(f"  Reason: {reason}")
        # Also surface scenario outcomes briefly
        for sc in r.get("scenarios", []):
            label = sc.get("label")
            sc_status = sc.get("status", "")
            code = sc.get("status_code")
            if label:
                print(f"   - {label}: {code if code is not None else sc_status}")
    print("-----------------------------------\n")


def parse_args():
    p = argparse.ArgumentParser(description="Auto-generate and run integration tests for EMPI endpoints via OpenAPI")
    p.add_argument("--concurrency", type=int, default=5, help="Number of concurrent requests")
    p.add_argument("--report", type=str, default="empi_test_report.json", help="Path to save JSON report")
    p.add_argument("--filter", type=str, default=None, help="Filter operations by text (operationId/summary)")
    p.add_argument("--x-group", dest="x_group", type=str, default=DEFAULT_X_GROUP, help="x-group header (overrides env)")
    p.add_argument("--x-hospital", dest="x_hospital", type=str, default=DEFAULT_X_HOSPITAL, help="x-hospital header (overrides env)")
    return p.parse_args()


async def main_async():
    # Load OpenAPI
    spec = await fetch_openapi_spec()
    if not isinstance(spec, dict) or "error" in spec:
        print(f"Failed to load OpenAPI: {spec.get('error') if isinstance(spec, dict) else spec}")
        return 2

    args = parse_args()
    report = await test_all_endpoints(spec, args.concurrency, args.x_group, args.x_hospital, args.filter)

    # Write JSON report
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Print human-friendly summary
    print_human_report(report)

    failed = report.get("summary", {}).get("failed", 0)
    return 1 if failed else 0


def main():
    try:
        exit_code = asyncio.run(main_async())
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        print(f"Unhandled error: {e}")
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
