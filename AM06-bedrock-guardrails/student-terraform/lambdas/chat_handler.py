"""OrbitOps chat handler (AWS Lambda behind a Function URL).

Serves the OrbitOps chat console (GET /) and relays each chat message to the
orchestrator agent running in Amazon Bedrock AgentCore Runtime (POST /chat).

The authentication chain this function implements is the one Mission A repairs:

  browser -> Function URL -> this Lambda
     -> Amazon Cognito /oauth2/token  (client_credentials grant, M2M app client)
     -> AgentCore Runtime HTTPS data plane with  Authorization: Bearer <access token>

Environment variables (all five must be set for the chain to work):
  ORCHESTRATOR_RUNTIME_ARN  ARN of the orchestrator AgentCore Runtime
  COGNITO_DOMAIN            e.g. orbit-am01-12345678.auth.us-west-2.amazoncognito.com
  COGNITO_CLIENT_ID         M2M app client id (client_credentials grant)
  COGNITO_CLIENT_SECRET     its client secret (confidential client)
  COGNITO_USER_POOL_ID      user pool id (used for the OIDC discovery health check)
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

REGION = os.environ.get("AWS_REGION", "us-west-2")
RUNTIME_ARN = os.environ.get("ORCHESTRATOR_RUNTIME_ARN", "").strip()
COGNITO_DOMAIN = os.environ.get("COGNITO_DOMAIN", "").strip()
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "").strip()
COGNITO_CLIENT_SECRET = os.environ.get("COGNITO_CLIENT_SECRET", "").strip()
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "").strip()
STUDENT_ID = os.environ.get("STUDENT_ID", "")
MODULE = os.environ.get("MODULE", "")

_token_cache = {"token": None, "expires": 0.0}


class AuthUnavailable(Exception):
    pass


def _http(method, url, headers=None, body=None, timeout=100):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def get_token():
    missing = [n for n, v in (("COGNITO_DOMAIN", COGNITO_DOMAIN), ("COGNITO_CLIENT_ID", COGNITO_CLIENT_ID), ("COGNITO_CLIENT_SECRET", COGNITO_CLIENT_SECRET)) if not v]
    if missing:
        raise AuthUnavailable(f"missing environment configuration: {', '.join(missing)}")
    now = time.time()
    if _token_cache["token"] and _token_cache["expires"] - 60 > now:
        return _token_cache["token"]
    domain = COGNITO_DOMAIN if COGNITO_DOMAIN.startswith("http") else f"https://{COGNITO_DOMAIN}"
    basic = base64.b64encode(f"{COGNITO_CLIENT_ID}:{COGNITO_CLIENT_SECRET}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": "supplychain/read supplychain/write"}).encode()
    try:
        status, _, raw = _http("POST", f"{domain}/oauth2/token", {"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"}, body, timeout=15)
    except Exception as e:  # DNS failure etc.
        raise AuthUnavailable(f"could not reach Cognito token endpoint {domain}/oauth2/token: {e}") from e
    if status != 200:
        raise AuthUnavailable(f"Cognito token endpoint returned {status}: {raw[:200].decode('utf-8', 'replace')}")
    tok = json.loads(raw)
    _token_cache["token"] = tok["access_token"]
    _token_cache["expires"] = now + int(tok.get("expires_in", 3600))
    return _token_cache["token"]


def invoke_orchestrator(token, prompt, actor_id, session_id):
    if not RUNTIME_ARN:
        raise RuntimeError("ORCHESTRATOR_RUNTIME_ARN is not configured on the chat handler")
    url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{urllib.parse.quote(RUNTIME_ARN, safe='')}/invocations?qualifier=DEFAULT"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        "X-Amzn-Bedrock-AgentCore-Runtime-User-Id": actor_id,
    }
    payload = json.dumps({"prompt": prompt, "actorId": actor_id, "sessionId": session_id}).encode()
    status, resp_headers, raw = _http("POST", url, headers, payload)
    text = raw.decode("utf-8", "replace")
    if status in (401, 403):
        raise AuthUnavailable(f"the orchestrator runtime rejected the token (HTTP {status}, {resp_headers.get('x-amzn-ErrorType', '')}). Is this client id in the runtime's JWT authorizer allowedClients? {text[:200]}")
    if status != 200:
        raise RuntimeError(f"orchestrator runtime returned HTTP {status} ({resp_headers.get('x-amzn-ErrorType', '')}): {text[:400]}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"status": "success", "response": text}


def _resp(status, body, content_type="application/json"):
    return {
        "statusCode": status,
        "headers": {"content-type": content_type, "cache-control": "no-store", "access-control-allow-origin": "*"},
        "body": body if isinstance(body, str) else json.dumps(body, default=str),
    }


def health():
    info = {
        "student": STUDENT_ID,
        "module": MODULE,
        "region": REGION,
        "config": {
            "ORCHESTRATOR_RUNTIME_ARN": "set" if RUNTIME_ARN else "MISSING",
            "COGNITO_DOMAIN": "set" if COGNITO_DOMAIN else "MISSING",
            "COGNITO_CLIENT_ID": "set" if COGNITO_CLIENT_ID else "MISSING",
            "COGNITO_CLIENT_SECRET": "set" if COGNITO_CLIENT_SECRET else "MISSING",
            "COGNITO_USER_POOL_ID": "set" if COGNITO_USER_POOL_ID else "MISSING",
        },
    }
    if COGNITO_USER_POOL_ID:
        disc = f"https://cognito-idp.{REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/openid-configuration"
        try:
            s, _, _ = _http("GET", disc, timeout=10)
            info["cognito_discovery"] = {"url": disc, "http_status": s}
        except Exception as e:
            info["cognito_discovery"] = {"url": disc, "error": str(e)}
    try:
        get_token()
        info["token"] = "ok"
    except AuthUnavailable as e:
        info["token"] = f"Authentication service unavailable: {e}"
    return info


def handler(event, context):
    rc = event.get("requestContext", {}).get("http", {})
    method, path = rc.get("method", "GET"), event.get("rawPath", "/")
    path = re.sub(r"/{2,}", "/", path)  # the Function URL ends in "/", so "$URL/health" arrives as "//health"
    if method == "OPTIONS":
        return _resp(204, "")
    if method == "GET" and path in ("/", "/index.html"):
        return _resp(200, PAGE.replace("__STUDENT__", STUDENT_ID).replace("__MODULE__", MODULE), "text/html; charset=utf-8")
    if method == "GET" and path.startswith("/health"):
        return _resp(200, health())
    if method == "POST" and path.startswith("/chat"):
        body = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return _resp(400, {"error": "body must be JSON"})
        prompt = str(data.get("prompt", "")).strip()
        actor = re.sub(r"[^a-zA-Z0-9_\-]", "-", str(data.get("actorId") or "captain-nova"))[:60] or "captain-nova"
        session = re.sub(r"[^a-zA-Z0-9_\-]", "-", str(data.get("sessionId") or ""))
        if len(session) < 33:  # AgentCore runtime session ids must be at least 33 characters
            session = f"orbit-{actor}-{uuid.uuid4().hex}"[:100]
        if not prompt:
            return _resp(400, {"error": "prompt is required"})
        try:
            token = get_token()
            result = invoke_orchestrator(token, prompt, actor, session)
            result.setdefault("sessionId", session)
            result.setdefault("actorId", actor)
            return _resp(200, result)
        except AuthUnavailable as e:
            return _resp(503, {"status": "error", "error": "Authentication service unavailable", "detail": str(e), "sessionId": session})
        except Exception as e:  # noqa: BLE001
            return _resp(502, {"status": "error", "error": f"{type(e).__name__}: {e}", "sessionId": session})
    return _resp(404, {"error": "not found", "routes": ["GET /", "GET /health", "POST /chat"]})


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>OrbitOps Flight Control</title>
<style>
:root{--bg:#070b1a;--panel:#0f1630;--line:#22305a;--ink:#e6ecff;--muted:#8d9bc7;--cyan:#38e1ff;--amber:#ffb545;--red:#ff5c7a;--green:#4ff0a5}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(1200px 600px at 20% -10%,#1a2a6c 0%,var(--bg) 55%);color:var(--ink);font:15px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;min-height:100vh}
.stars{position:fixed;inset:0;pointer-events:none;background-image:radial-gradient(1px 1px at 20% 30%,#fff 50%,transparent 51%),radial-gradient(1px 1px at 70% 20%,#fff 50%,transparent 51%),radial-gradient(1.5px 1.5px at 40% 80%,#9cf 50%,transparent 51%),radial-gradient(1px 1px at 85% 65%,#fff 50%,transparent 51%),radial-gradient(1px 1px at 10% 90%,#fff 50%,transparent 51%),radial-gradient(1.2px 1.2px at 55% 50%,#ffd 50%,transparent 51%);opacity:.6}
header{display:flex;align-items:center;gap:14px;padding:18px 24px;border-bottom:1px solid var(--line);background:rgba(7,11,26,.7);backdrop-filter:blur(6px);position:sticky;top:0}
header h1{font-size:18px;margin:0;letter-spacing:.08em;text-transform:uppercase}header h1 span{color:var(--cyan)}
.badge{font-size:12px;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:3px 10px}
main{max-width:980px;margin:0 auto;padding:20px;display:grid;grid-template-columns:1fr 300px;gap:18px}
@media(max-width:820px){main{grid-template-columns:1fr}}
.panel{background:rgba(15,22,48,.9);border:1px solid var(--line);border-radius:14px;padding:14px}
#log{height:56vh;overflow:auto;display:flex;flex-direction:column;gap:10px;padding:6px}
.msg{max-width:85%;padding:10px 14px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}
.me{align-self:flex-end;background:#1d2b63;border:1px solid #2f4390}.bot{align-self:flex-start;background:#111c3d;border:1px solid var(--line)}
.err{align-self:flex-start;background:#3a1220;border:1px solid var(--red);color:#ffd5dd}
.meta{font-size:11px;color:var(--muted);margin-top:6px}
details{margin-top:6px;font-size:12px;color:var(--muted)}details pre{white-space:pre-wrap;background:#0a1026;padding:8px;border-radius:8px;max-height:220px;overflow:auto}
form{display:flex;gap:8px;margin-top:10px}input,button,select{font:inherit}
input[type=text]{flex:1;background:#0a1026;border:1px solid var(--line);color:var(--ink);border-radius:10px;padding:10px 12px}
button{background:var(--cyan);color:#06111f;border:0;border-radius:10px;padding:10px 16px;font-weight:700;cursor:pointer}button.ghost{background:transparent;color:var(--muted);border:1px solid var(--line)}
label{display:block;font-size:12px;color:var(--muted);margin:10px 0 4px}
.side input{width:100%;background:#0a1026;border:1px solid var(--line);color:var(--ink);border-radius:10px;padding:8px 10px}
.pill{display:inline-block;font-size:11px;border-radius:999px;padding:2px 8px;margin:2px 2px 0 0;border:1px solid var(--line);color:var(--muted)}
.pill.on{color:var(--green);border-color:var(--green)}.pill.off{color:var(--amber);border-color:var(--amber)}
.hint{font-size:12px;color:var(--muted);margin-top:12px;line-height:1.5}
</style></head><body><div class="stars"></div>
<header><div style="font-size:26px">🛰️</div><div><h1>Orbit<span>Ops</span> Flight Control</h1><div class="badge">student: __STUDENT__ · module: __MODULE__</div></div></header>
<main>
 <section class="panel">
  <div id="log"></div>
  <form id="f"><input id="q" type="text" autocomplete="off" placeholder="Flight control, are you receiving my cargo dispatch requests?"><button>Send</button></form>
 </section>
 <aside class="panel side">
  <label>Operator (actorId)</label><input id="actor" value="captain-nova">
  <label>Session</label><input id="session" readonly>
  <div style="margin-top:8px"><button class="ghost" type="button" id="newsess">New session</button></div>
  <label>Last reply trace</label><div id="pills"></div>
  <div class="hint">Try: <em>Check our cargo stock levels</em> · <em>Where is shipment SHP-1001?</em> · <em>Show the status of cargo quality inspections</em> · <em>What do our procedures say about inventory checks?</em> · <em>I prefer detailed shipment reports</em></div>
 </aside>
</main>
<script>
const log=document.getElementById('log'),q=document.getElementById('q'),actor=document.getElementById('actor'),sess=document.getElementById('session'),pills=document.getElementById('pills');
function uuid(){return ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g,c=>(c^crypto.getRandomValues(new Uint8Array(1))[0]&15>>c/4).toString(16))}
function newSession(){sess.value='orbit-'+uuid().replace(/-/g,'');try{sessionStorage.setItem('orbit-session',sess.value)}catch(e){};log.innerHTML='';add('bot','New session '+sess.value.slice(0,18)+'… started. The flight computer has no short-term context from earlier sessions unless long-term memory is wired up.')}
function md(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/^(\s*)[-*] /gm,'$1• ')}
function add(kind,text,trace){const d=document.createElement('div');d.className='msg '+kind;if(kind==='bot')d.innerHTML=md(text);else d.textContent=text;if(trace){const det=document.createElement('details');det.innerHTML='<summary>trace</summary><pre>'+JSON.stringify(trace,null,2).replace(/</g,'&lt;')+'</pre>';d.appendChild(det)}log.appendChild(d);log.scrollTop=log.scrollHeight}
function showPills(t){pills.innerHTML='';if(!t)return;const items=[['gateway',t.gateway_configured,(t.tools_discovered||0)+' tools'],['tools called',(t.tools_called||[]).length>0,(t.tools_called||[]).map(x=>x.tool).join(', ')||'none'],['memory',t.memory_configured,(t.memory_hits||[]).filter(h=>h.text).length+' hits'],['specialist',t.specialist_consulted,t.specialist_configured?'configured':'not configured'],['guardrail',t.guardrail_configured,t.guardrail_action||'n/a']];for(const [n,on,v] of items){const s=document.createElement('span');s.className='pill '+(on?'on':'off');s.textContent=n+': '+v;pills.appendChild(s)}}
try{const saved=sessionStorage.getItem('orbit-session');if(saved)sess.value=saved;else newSession()}catch(e){newSession()}
document.getElementById('newsess').onclick=newSession;
document.getElementById('f').onsubmit=async e=>{e.preventDefault();const text=q.value.trim();if(!text)return;add('me',text);q.value='';const w=document.createElement('div');w.className='msg bot';w.textContent='… contacting flight controller';log.appendChild(w);
 try{const r=await fetch('/chat',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({prompt:text,actorId:actor.value||'captain-nova',sessionId:sess.value})});const j=await r.json();w.remove();
  if(!r.ok||j.status==='error'){add('err','⚠ '+(j.error||('HTTP '+r.status))+(j.detail?'\n'+j.detail:''),j.trace);showPills(j.trace);return}
  add('bot',j.response||JSON.stringify(j),j.trace);showPills(j.trace)}catch(err){w.remove();add('err','⚠ request failed: '+err)}};
</script></body></html>"""
