#!/usr/bin/env python3
"""Small MCP stdio server. Only instructor-authorized tools are advertised."""
import argparse
import json
from pathlib import Path
import sys

# The launcher uses Python isolated mode; add only the instructor module directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import Broker, InfrastructureError, PolicyError, load_profile

MAX_REQUEST = 262144


def tools_for(broker):
    specs = {
        "apply_patch": {"name": "apply_patch", "description": "Replace one allowed UTF-8 file. Paths are relative to the exercise. This does not run shell patch syntax.",
          "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}},
        "run_tests": {"name": "run_tests", "description": "Run one instructor-listed practice test in a credential-free, no-network Docker worker. Advisory, not an official grade.",
          "inputSchema": {"type": "object", "properties": {"testId": {"type": "string", "enum": list(broker.profile["tests"])}}, "required": ["testId"], "additionalProperties": False}},
    }
    return [specs[name] for name in broker.tool_names()]


def dispatch(request, broker):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    if "id" not in request:
        return None
    response = {"jsonrpc": "2.0", "id": request["id"]}
    method, params = request.get("method"), request.get("params", {})
    try:
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "tensorlearn-authoring", "version": "0.1.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tools_for(broker)}
        elif method == "tools/call":
            if not isinstance(params, dict) or set(params) - {"name", "arguments", "_meta"}:
                raise PolicyError("Invalid tool call fields.")
            name, arguments = params.get("name"), params.get("arguments", {})
            if name not in broker.tool_names() or not isinstance(arguments, dict):
                raise PolicyError("Tool is unavailable in this profile.")
            if name == "apply_patch" and set(arguments) == {"path", "content"}:
                value = broker.apply_patch(**arguments)
            elif name == "run_tests" and set(arguments) == {"testId"}:
                value = broker.run_tests(arguments["testId"])
            else:
                raise PolicyError("Invalid tool arguments.")
            result = {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}
        else:
            return {**response, "error": {"code": -32601, "message": "Method not found"}}
        return {**response, "result": result}
    except (PolicyError, InfrastructureError, TypeError) as error:
        return {**response, "result": {"content": [{"type": "text", "text": str(error)}], "isError": True}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--exercise", required=True)
    parser.add_argument("--image")
    args = parser.parse_args()
    broker = Broker(args.workspace, load_profile(args.exercise), args.image)
    while True:
        line = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if not line:
            return
        if len(line) > MAX_REQUEST:
            raise PolicyError("MCP request exceeds the limit.")
        try:
            request = json.loads(line)
            response = dispatch(request, broker)
        except (ValueError, RecursionError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if response is not None:
            print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
