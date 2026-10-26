"""OrbitOps knowledge-base specialist (research droid).

A second AgentCore Runtime agent that the orchestrator calls agent-to-agent.
It answers questions strictly from a Bedrock Knowledge Base (Mission C).

  POST /invocations  {"prompt": "..."}  ->  {"status": "success", "response": "...", "sources": [...]}
  GET  /ping         {"status": "Healthy"}

Environment:
  MODEL_ID           Bedrock model used to write the answer from retrieved passages
  KNOWLEDGE_BASE_ID  Bedrock Knowledge Base id to retrieve from (set by the student in Mission C)
"""

import json
import logging
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
from botocore.config import Config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
log = logging.getLogger("kb-specialist")

REGION = os.environ.get("ORBIT_REGION") or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
MODEL_ID = os.environ.get("MODEL_ID", "us.amazon.nova-2-lite-v1:0")
KNOWLEDGE_BASE_ID = os.environ.get("KNOWLEDGE_BASE_ID", "").strip()
NUM_RESULTS = int(os.environ.get("NUM_RESULTS", "4"))

# Fail fast on an unreachable endpoint (e.g. a missing VPC interface endpoint) so the error reaches the trace
# well before the callers give up; the default 60 s connect timeout x 3 attempts would hang for minutes.
_cfg = Config(region_name=REGION, retries={"max_attempts": 2, "mode": "standard"}, connect_timeout=5, read_timeout=60)
_kb = boto3.client("bedrock-agent-runtime", config=_cfg)
_bedrock = boto3.client("bedrock-runtime", config=_cfg)

SYSTEM = (
    "You are the OrbitOps knowledge-base specialist. Answer ONLY from the retrieved passages. "
    "Quote the relevant procedure or rule, name the document it came from, and say 'The manuals do not cover that' "
    "if the passages do not answer the question. Be brief."
)


def answer(question):
    if not KNOWLEDGE_BASE_ID:
        raise RuntimeError("KNOWLEDGE_BASE_ID is not configured on the specialist runtime")
    resp = _kb.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": question},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": NUM_RESULTS}},
    )
    passages, sources = [], []
    for r in resp.get("retrievalResults", []):
        text = (r.get("content") or {}).get("text", "")
        loc = r.get("location") or {}
        uri = (loc.get("s3Location") or {}).get("uri") or json.dumps(loc)
        passages.append(f"[source: {uri}]\n{text}")
        sources.append({"uri": uri, "score": r.get("score"), "excerpt": text[:240]})
    if not passages:
        return "The manuals do not contain anything relevant to that question (0 passages retrieved).", sources
    prompt = "Question: " + question + "\n\nRetrieved passages:\n\n" + "\n\n".join(passages)
    out = _bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 700, "temperature": 0.1},
    )
    text = "\n".join(c["text"] for c in out["output"]["message"]["content"] if "text" in c).strip()
    return text, sources


class Handler(BaseHTTPRequestHandler):
    server_version = "OrbitOpsKBSpecialist/1.0"

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
        question = str((payload.get("prompt") if isinstance(payload, dict) else payload) or "").strip()
        if not question:
            return self._send(200, {"status": "error", "error": "Send {\"prompt\": \"...\"}"})
        try:
            text, sources = answer(question)
            return self._send(200, {"status": "success", "response": text, "sources": sources, "knowledge_base_id": KNOWLEDGE_BASE_ID, "agent": "kb-specialist"})
        except Exception as e:  # noqa: BLE001
            log.error("specialist failed: %s\n%s", e, traceback.format_exc())
            return self._send(200, {"status": "error", "response": f"Specialist fault: {type(e).__name__}: {e}", "error": f"{type(e).__name__}: {e}", "agent": "kb-specialist"})

    def log_message(self, fmt, *args):
        log.info("%s " + fmt, self.address_string(), *args)


if __name__ == "__main__":
    log.info("starting kb specialist region=%s model=%s kb=%s", REGION, MODEL_ID, KNOWLEDGE_BASE_ID or "(unset)")
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
