"""OrbitOps flight-controller orchestrator.

Runs inside Amazon Bedrock AgentCore Runtime (direct code deployment, HTTP protocol).
Deliberately dependency-light: Python standard library + boto3 (vendored into the zip).

Contract (https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html):
  POST /invocations  -> JSON body {"prompt": "...", "actorId": "...", "sessionId": "..."}
  GET  /ping         -> {"status": "Healthy"}

Everything the agent can do is switched on by environment variables that the
labs ask students to repair:

  MODEL_ID                   Bedrock model used for reasoning (Converse API)
  GATEWAY_URL                AgentCore Gateway MCP endpoint -> tool discovery (Mission B)
  KB_SPECIALIST_RUNTIME_ARN  AgentCore Runtime ARN of the knowledge-base specialist (Mission C)
  MEMORY_ID                  AgentCore Memory resource id (Mission D)
  GUARDRAIL_ID / GUARDRAIL_VERSION  Bedrock guardrail applied to every model call (Mission F)

Authentication: the chat handler calls this runtime with a Cognito OAuth 2.0
bearer token. The runtime's request-header allowlist forwards that
`Authorization` header to us, and we forward the same token to the Gateway and
to the specialist runtime (token pass-through). If no inbound token is present
and COGNITO_DOMAIN / COGNITO_CLIENT_ID / COGNITO_CLIENT_SECRET are set, the
agent fetches its own client_credentials token instead.
"""

import base64
import datetime as dt
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
from botocore.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("orchestrator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REGION = (
    os.environ.get("ORBIT_REGION")
    or os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or "us-west-2"
)
MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-2-lite-v1:0")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "").strip()
KB_SPECIALIST_RUNTIME_ARN = os.environ.get("KB_SPECIALIST_RUNTIME_ARN", "").strip()
MEMORY_ID = os.environ.get("MEMORY_ID", "").strip()
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "").strip()
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "DRAFT").strip() or "DRAFT"
COGNITO_DOMAIN = os.environ.get("COGNITO_DOMAIN", "").strip()
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "").strip()
COGNITO_CLIENT_SECRET = os.environ.get("COGNITO_CLIENT_SECRET", "").strip()
MAX_TOOL_ROUNDS = int(os.environ.get("MAX_TOOL_ROUNDS", "6"))

MEMORY_NAMESPACES = {
    "preferences": "supplychain/user/{actorId}/preferences",
    "semantic": "supplychain/user/{actorId}/semantic",
}

SYSTEM_PROMPT = """You are the OrbitOps flight controller: the AI operations assistant for a
space cargo supply-chain company. Operators ask you about cargo inventory, suppliers,
shipments in transit, and quality inspections.

Rules:
- When a tool exists for the question, ALWAYS call the tool and answer from its data. Never invent
  stock numbers, shipment positions, supplier names, or inspection results.
- Questions about OrbitOps procedures, manuals or policy documents ('what do our procedures say
  about ...') must be delegated to the consult_manuals tool when it is available. When it is not
  available, answer 'I have no manuals specialist connected, so I cannot look that up.'
- Questions about the operator themselves (their preferences, how they like reports formatted)
  are answered from the known facts about this operator below. If there are none, say you have
  no record of that preference yet.
- If no tool can answer, say plainly which capability is not connected (for example: 'I have no
  quality-inspection tool connected').
- If the operator is only checking the link (for example 'do you copy?' or 'are you receiving me?'),
  confirm you are online and list what you can help with; no tool is needed for that.
- Be concise and operational. Use short bullet points for lists. Address the operator by name if known.
- Always speak directly to the operator. Never describe your plan, your reasoning or the names of your tools.
"""

_boto_cfg = Config(region_name=REGION, retries={"max_attempts": 2, "mode": "standard"}, connect_timeout=5, read_timeout=60)
_bedrock = boto3.client("bedrock-runtime", config=_boto_cfg)
_memory = boto3.client("bedrock-agentcore", config=_boto_cfg) if MEMORY_ID else None

_token_cache = {"token": None, "expires": 0.0}
_tool_cache = {"tools": None, "fetched": 0.0, "mcp_session": None}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers: HTTP + auth
# ---------------------------------------------------------------------------
def _http(method, url, headers=None, body=None, timeout=90):
    data = None
    if body is not None:
        data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _client_credentials_token():
    """Fallback: mint our own M2M token (only when no inbound bearer token exists)."""
    if not (COGNITO_DOMAIN and COGNITO_CLIENT_ID and COGNITO_CLIENT_SECRET):
        return None
    now = time.time()
    if _token_cache["token"] and _token_cache["expires"] - 60 > now:
        return _token_cache["token"]
    domain = COGNITO_DOMAIN if COGNITO_DOMAIN.startswith("http") else f"https://{COGNITO_DOMAIN}"
    basic = base64.b64encode(f"{COGNITO_CLIENT_ID}:{COGNITO_CLIENT_SECRET}".encode()).decode()
    body = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "scope": "supplychain/read supplychain/write"}
    ).encode()
    status, _, raw = _http(
        "POST",
        f"{domain}/oauth2/token",
        {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        body,
    )
    if status != 200:
        raise RuntimeError(f"Cognito token request failed ({status}): {raw[:300]!r}")
    tok = json.loads(raw)
    _token_cache["token"] = tok["access_token"]
    _token_cache["expires"] = now + int(tok.get("expires_in", 3600))
    return _token_cache["token"]


def _bearer_for_downstream(inbound_token):
    if inbound_token:
        return inbound_token
    return _client_credentials_token()


# ---------------------------------------------------------------------------
# MCP client (AgentCore Gateway) - minimal JSON-RPC over HTTP
# ---------------------------------------------------------------------------
def _parse_mcp_response(headers, raw):
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    text = raw.decode("utf-8", "replace")
    if "text/event-stream" in ctype:
        last = None
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    try:
                        last = json.loads(payload)
                    except json.JSONDecodeError:
                        pass
        return last or {}
    return json.loads(text) if text.strip() else {}


def _mcp_call(token, method, params, rpc_id):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if _tool_cache.get("mcp_session"):
        headers["Mcp-Session-Id"] = _tool_cache["mcp_session"]
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
    status, resp_headers, raw = _http("POST", GATEWAY_URL, headers, body, timeout=30)
    if status in (401, 403):
        raise RuntimeError(f"Gateway rejected our token ({status}). Check the gateway's JWT authorizer allowedClients. Body: {raw[:200]!r}")
    if status >= 400:
        raise RuntimeError(f"Gateway MCP call {method} failed ({status}): {raw[:300]!r}")
    sid = resp_headers.get("Mcp-Session-Id") or resp_headers.get("mcp-session-id")
    if sid:
        _tool_cache["mcp_session"] = sid
    msg = _parse_mcp_response(resp_headers, raw)
    if "error" in msg:
        raise RuntimeError(f"Gateway MCP error for {method}: {msg['error']}")
    return msg.get("result", {})


def discover_tools(token, force=False):
    """Return Bedrock Converse toolSpecs discovered from the Gateway (cached 60s)."""
    if not GATEWAY_URL:
        return []
    with _lock:
        if not force and _tool_cache["tools"] is not None and time.time() - _tool_cache["fetched"] < 60:
            return _tool_cache["tools"]
        _mcp_call(
            token,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "orbitops-orchestrator", "version": "1.0"},
            },
            1,
        )
        result = _mcp_call(token, "tools/list", {}, 2)
        specs, aliases = [], {}
        for t in result.get("tools", []):
            name = t.get("name", "")
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name):
                log.warning("Skipping tool with unsupported name %r", name)
                continue
            # Amazon Nova (v1) fails tool calls whose name contains "-" ("Model produced invalid
            # sequence as part of ToolUse"), and gateway tools are named "<target-name>___<tool>".
            # Offer the model a hyphen-free alias and map it back to the MCP name in run_turn.
            alias = name.replace("-", "_")
            aliases[alias] = name
            schema = t.get("inputSchema") or {"type": "object", "properties": {}}
            specs.append(
                {
                    "toolSpec": {
                        "name": alias,
                        "description": (t.get("description") or name)[:1000],
                        "inputSchema": {"json": schema},
                    }
                }
            )
        _tool_cache["tools"] = specs
        _tool_cache["aliases"] = aliases
        _tool_cache["fetched"] = time.time()
        log.info("Discovered %d gateway tools: %s", len(specs), list(aliases.values()))
        return specs


def call_gateway_tool(token, name, arguments):
    result = _mcp_call(token, "tools/call", {"name": name, "arguments": arguments or {}}, 3)
    parts = []
    for c in result.get("content", []):
        if c.get("type") == "text":
            parts.append(c.get("text", ""))
        else:
            parts.append(json.dumps(c))
    text = "\n".join(parts) if parts else json.dumps(result)
    return text, bool(result.get("isError"))


# ---------------------------------------------------------------------------
# Knowledge-base specialist (agent-to-agent over the AgentCore Runtime HTTP data plane)
# ---------------------------------------------------------------------------
SPECIALIST_TOOL = {
    "toolSpec": {
        "name": "consult_manuals",
        "description": "Ask the knowledge-base specialist agent a question about OrbitOps procedures, manuals, policies or reference documents (inventory procedures, supplier manual, quality control manual). Returns an answer with source excerpts.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {"question": {"type": "string", "description": "The question to answer from the manuals"}},
                "required": ["question"],
            }
        },
    }
}


def consult_specialist(token, question, session_id):
    if not token:
        raise RuntimeError("No bearer token available to call the specialist runtime")
    escaped = urllib.parse.quote(KB_SPECIALIST_RUNTIME_ARN, safe="")
    url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{escaped}/invocations?qualifier=DEFAULT"
    spec_session = f"spec-{session_id}"[:120]
    if len(spec_session) < 33:
        spec_session = (spec_session + "-" + uuid.uuid4().hex)[:120]
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": spec_session,
    }
    status, _, raw = _http("POST", url, headers, {"prompt": question}, timeout=75)  # < the chat handler's 100 s
    if status != 200:
        raise RuntimeError(f"Specialist runtime returned {status}: {raw[:300]!r}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"response": raw.decode("utf-8", "replace")}
    return data


# ---------------------------------------------------------------------------
# AgentCore Memory
# ---------------------------------------------------------------------------
def recall(actor_id, query):
    """Pull long-term memory records (preferences + semantic facts) for this actor."""
    hits = []
    if not _memory:
        return hits
    for kind, template in MEMORY_NAMESPACES.items():
        ns = template.replace("{actorId}", actor_id)
        try:
            resp = _memory.retrieve_memory_records(
                memoryId=MEMORY_ID,
                namespace=ns,
                searchCriteria={"searchQuery": query, "topK": 5},
            )
            for rec in resp.get("memoryRecordSummaries", []):
                text = (rec.get("content") or {}).get("text")
                if text:
                    hits.append({"kind": kind, "text": text, "score": rec.get("score")})
        except Exception as e:  # noqa: BLE001 - surface in trace, never crash the turn
            log.warning("memory recall failed for %s: %s", ns, e)
            hits.append({"kind": kind, "error": str(e)[:200]})
    return hits


def remember(actor_id, session_id, user_text, assistant_text):
    if not _memory:
        return None
    try:
        _memory.create_event(
            memoryId=MEMORY_ID,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=dt.datetime.now(dt.timezone.utc),
            payload=[
                {"conversational": {"content": {"text": user_text}, "role": "USER"}},
                {"conversational": {"content": {"text": assistant_text}, "role": "ASSISTANT"}},
            ],
        )
        return "stored"
    except Exception as e:  # noqa: BLE001
        log.warning("memory write failed: %s", e)
        return f"error: {str(e)[:200]}"


# ---------------------------------------------------------------------------
# The reasoning loop (Bedrock Converse with tools + optional guardrail)
# ---------------------------------------------------------------------------
def run_turn(prompt, actor_id, session_id, inbound_token):
    trace = {
        "model": MODEL_ID,
        "gateway_configured": bool(GATEWAY_URL),
        "tools_discovered": 0,
        "tools_called": [],
        "specialist_configured": bool(KB_SPECIALIST_RUNTIME_ARN),
        "specialist_consulted": False,
        "memory_configured": bool(MEMORY_ID),
        "memory_hits": [],
        "memory_write": None,
        "guardrail_configured": bool(GUARDRAIL_ID),
        "guardrail_action": None,
        "token_source": "inbound" if inbound_token else ("client_credentials" if COGNITO_CLIENT_ID else "none"),
        "warnings": [],
    }

    token = None
    try:
        token = _bearer_for_downstream(inbound_token)
    except Exception as e:  # noqa: BLE001
        trace["warnings"].append(f"token: {e}")

    tools = []
    if GATEWAY_URL:
        try:
            tools = list(discover_tools(token)) if token else []
            if not token:
                trace["warnings"].append("gateway: no bearer token available for tool discovery")
        except Exception as e:  # noqa: BLE001
            trace["warnings"].append(f"gateway: {e}")
    trace["tools_discovered"] = len(tools)
    if KB_SPECIALIST_RUNTIME_ARN:
        tools.append(SPECIALIST_TOOL)

    memory_hits = recall(actor_id, prompt)
    trace["memory_hits"] = memory_hits
    facts = [h["text"] for h in memory_hits if h.get("text")]
    system = SYSTEM_PROMPT
    if facts:
        system += "\nKnown facts about this operator (from long-term memory):\n- " + "\n- ".join(facts) + "\n"
    system += f"\nThe operator's id is '{actor_id}'."
    # Say which tools exist this turn: with none, the model otherwise "narrates" imaginary tool calls.
    connected = sorted({(_tool_cache.get("aliases") or {}).get(t["toolSpec"]["name"], t["toolSpec"]["name"]).split("___")[0] for t in tools})
    system += (
        "\nTools connected right now: " + (", ".join(connected) if connected else "NONE")
        + ". If the operator asks for something no connected tool provides, say plainly that this capability is"
        " not connected. Never invent data and never pretend to call a tool.\n"
    )

    messages = [{"role": "user", "content": [{"text": prompt}]}]
    converse_kwargs = {
        "modelId": MODEL_ID,
        "system": [{"text": system}],
        "inferenceConfig": {"maxTokens": 1024, "temperature": 0.1},
    }
    if tools:
        converse_kwargs["toolConfig"] = {"tools": tools}
    if GUARDRAIL_ID:
        converse_kwargs["guardrailConfig"] = {
            "guardrailIdentifier": GUARDRAIL_ID,
            "guardrailVersion": GUARDRAIL_VERSION,
            "trace": "enabled",
        }

    final_text, nudged = "", False
    for _round in range(MAX_TOOL_ROUNDS + 1):
        resp = _bedrock.converse(messages=messages, **converse_kwargs)
        if resp.get("stopReason") == "guardrail_intervened":
            trace["guardrail_action"] = "intervened"
            gtrace = (resp.get("trace") or {}).get("guardrail") or {}
            trace["guardrail_trace"] = _redact_matches(json.loads(json.dumps(gtrace, default=str))[:1]) if isinstance(gtrace, list) else _summarize_guardrail(gtrace)
        elif GUARDRAIL_ID:
            # ANONYMIZE lets the turn continue, so there is no "intervened" stop reason; the
            # assessment still says what was masked, and that is the evidence students need.
            gtrace = (resp.get("trace") or {}).get("guardrail") or {}
            if "ANONYMIZED" in json.dumps(gtrace, default=str) and trace["guardrail_action"] != "intervened":
                trace["guardrail_action"] = "masked"
                trace["guardrail_trace"] = _summarize_guardrail(gtrace) if isinstance(gtrace, dict) else _redact_matches(gtrace)
            elif trace["guardrail_action"] is None:
                trace["guardrail_action"] = "passed"
        out = resp["output"]["message"]
        messages.append(out)
        text_parts = [c["text"] for c in out.get("content", []) if "text" in c]
        tool_uses = [c["toolUse"] for c in out.get("content", []) if "toolUse" in c]
        if not tool_uses or resp.get("stopReason") != "tool_use":
            # Nova narrates its tool reasoning in <thinking> tags; the operator only needs the answer.
            raw = "\n".join(text_parts)
            final_text = re.sub(r"<thinking>.*?</thinking>", "", raw, flags=re.S).strip()
            if not final_text and "<thinking>" in raw and not nudged:
                # The whole reply was reasoning: ask once for the operator-facing answer.
                nudged = True
                messages.append({"role": "user", "content": [{"text": "Now write your reply to the operator."}]})
                continue
            if not final_text:
                final_text = re.sub(r"</?thinking>", "", raw).strip()
            break
        results = []
        for tu in tool_uses:
            name, args = tu["name"], tu.get("input") or {}
            name = (_tool_cache.get("aliases") or {}).get(name, name)  # back to the gateway's MCP tool name
            entry = {"tool": name, "args": args}
            try:
                if name == "consult_manuals":
                    data = consult_specialist(token, args.get("question", prompt), session_id)
                    trace["specialist_consulted"] = True
                    entry["result_preview"] = str(data.get("response", data))[:200]
                    content = json.dumps(data)
                    is_error = False
                else:
                    content, is_error = call_gateway_tool(token, name, args)
                    entry["result_preview"] = content[:200]
                entry["error"] = is_error
            except Exception as e:  # noqa: BLE001
                content, is_error = f"Tool call failed: {e}", True
                entry["error"] = str(e)[:300]
            trace["tools_called"].append(entry)
            results.append(
                {
                    "toolResult": {
                        "toolUseId": tu["toolUseId"],
                        "content": [{"text": content[:20000]}],
                        "status": "error" if is_error else "success",
                    }
                }
            )
        messages.append({"role": "user", "content": results})
    else:
        final_text = "I stopped after too many tool rounds without a final answer."

    if not final_text:
        final_text = "(no text in model response)"
    trace["memory_write"] = remember(actor_id, session_id, prompt, final_text)
    return final_text, trace


def _redact_matches(obj):
    """Replace every guardrail `match` value: the trace goes to the browser, and a match
    is the original sensitive text (Bedrock reports it unmasked by design)."""
    if isinstance(obj, dict):
        return {k: ("[redacted]" if k == "match" else _redact_matches(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_matches(v) for v in obj]
    return obj


def _summarize_guardrail(gtrace):
    """Compact view of the guardrail assessment (input + output) for the chat UI trace panel.

    Converse returns inputAssessment as {guardrailId: assessment} and
    outputAssessments as {guardrailId: [assessment, ...]}.
    """
    out = {}
    for side in ("inputAssessment", "outputAssessments"):
        block = gtrace.get(side)
        if not block:
            continue
        items = []
        for v in (block.values() if isinstance(block, dict) else block):
            items.extend(v if isinstance(v, list) else [v])
        summary = []
        for a in items:
            if not isinstance(a, dict):
                continue
            for policy in ("topicPolicy", "contentPolicy", "wordPolicy", "sensitiveInformationPolicy"):
                if policy in a:
                    summary.append({policy: _redact_matches(json.loads(json.dumps(a[policy], default=str)))})
        out[side] = summary
    return out


# ---------------------------------------------------------------------------
# HTTP server implementing the AgentCore Runtime contract
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "OrbitOpsOrchestrator/1.0"

    def _send(self, status, payload):
        body = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/ping"):
            return self._send(200, {"status": "Healthy"})
        return self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/invocations"):
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {"prompt": raw.decode("utf-8", "replace")}
        if isinstance(payload, str):
            payload = {"prompt": payload}
        prompt = str(payload.get("prompt") or payload.get("input") or payload.get("message") or "").strip()
        actor_id = str(payload.get("actorId") or self.headers.get("X-Amzn-Bedrock-AgentCore-Runtime-User-Id") or "captain-nova")
        actor_id = re.sub(r"[^a-zA-Z0-9_\-/]", "-", actor_id)[:100] or "captain-nova"
        session_id = str(
            payload.get("sessionId") or self.headers.get("X-Amzn-Bedrock-AgentCore-Runtime-Session-Id") or uuid.uuid4()
        )
        auth = self.headers.get("Authorization") or ""
        inbound_token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        if not prompt:
            return self._send(200, {"status": "error", "error": "Send {\"prompt\": \"...\"}"})
        started = time.time()
        try:
            text, trace = run_turn(prompt, actor_id, session_id, inbound_token)
            trace["latency_ms"] = int((time.time() - started) * 1000)
            trace["actorId"], trace["sessionId"] = actor_id, session_id
            log.info("turn ok actor=%s session=%s tools=%s", actor_id, session_id, [t["tool"] for t in trace["tools_called"]])
            return self._send(200, {"status": "success", "response": text, "trace": trace})
        except Exception as e:  # noqa: BLE001
            log.error("turn failed: %s\n%s", e, traceback.format_exc())
            return self._send(
                200,
                {
                    "status": "error",
                    "response": f"Flight controller fault: {type(e).__name__}: {e}",
                    "error": f"{type(e).__name__}: {e}",
                    "trace": {"actorId": actor_id, "sessionId": session_id, "model": MODEL_ID},
                },
            )

    def log_message(self, fmt, *args):  # quieter access log
        log.info("%s " + fmt, self.address_string(), *args)


def main():
    port = int(os.environ.get("PORT", "8080"))
    log.info(
        "starting orchestrator region=%s model=%s gateway=%s memory=%s specialist=%s guardrail=%s",
        REGION, MODEL_ID, bool(GATEWAY_URL), bool(MEMORY_ID), bool(KB_SPECIALIST_RUNTIME_ARN), bool(GUARDRAIL_ID),
    )
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
