<sub>[⬅ All missions](README.md)</sub>

# Getting Started: Your First Five Minutes

Do this once at the start of class. Afterwards, every mission works the same way.

## 1. Collect your mission card from your instructor

Your instructor gives you a **mission card** with your personal values. Keep it open in a tab.

| On your card | Looks like | What it's for |
|---|---|---|
| **Console sign-in link** | `https://<account>.signin.aws.amazon.com/console` | Where you sign in to the AWS console |
| **Username + temporary password** | `student-07` / `••••••••` | Your AWS console login |
| **Lab student id** | `student07` | The id built into the names of all your lab resources |
| **Chat console URL** | `https://abc123….lambda-url.us-west-2.on.aws/` | The OrbitOps app you're repairing |
| **Mission values** | ARNs, ids, URLs | Listed in each handout under *"What Is Already In Place"* |

> [!IMPORTANT]
> Your **login name** (`student-07`) and your **lab student id** (`student07`) look similar but aren't the same. Resource names use the lab id, which has **no hyphen**: `orbit-student07-am01-chat-handler`.

## 2. Sign in and set the region

1. Open the sign-in link and enter your username and password. If you're asked to set a new password, do it now.
2. Look at the **region selector** in the top-right corner and pick **US West (Oregon) `us-west-2`**.

   > If a console page is empty or says *"no resources"*, the region is wrong nine times out of ten.

3. Pin the services you'll use to the console's favourites bar (click the ☆ in the service search results): **Amazon Bedrock AgentCore**, **Amazon Cognito**, **Lambda**, **CloudWatch**, and later **Amazon Bedrock** and **VPC**.

## 3. Find *your* resources

Everyone in the class shares one AWS account and has their own copy of the platform. To find yours, type your lab id into the search/filter box of any console list.

| Resource | Where | Your copy is named like |
|---|---|---|
| Chat handler (the web app) | Lambda → Functions | `orbit-student07-am01-chat-handler` |
| Tool Lambdas | Lambda → Functions | `orbit-student07-am01-inventory`, `…-logistics`, … |
| Orchestrator agent | Bedrock AgentCore → Runtime | `orbit_student07_am01_orchestrator` (underscores) |
| Tool gateway | Bedrock AgentCore → Gateways | `orbit-student07-am01-gateway` |
| User pool | Cognito → User pools | `orbit-am01-…` (**shared by the whole class**, so edit only your own app clients) |

> [!CAUTION]
> Anything without your id in the name is either shared or belongs to a classmate. **Look, don't touch.**

## 4. Meet the chat console and its trace panel

Open your **chat console URL**. The header shows `student: student07 · module: am01`. Two things matter here:

**`<chat url>/health`** (open it in a new tab) shows the app's configuration as JSON: which settings are present and which are `MISSING`, and whether it could get a login token.

**The trace.** Every reply, including error replies, has a collapsible **trace** underneath, plus summary pills at the top. It's the flight recorder of what the agent actually did, and it's your most important evidence:

| Trace field | It tells you | Matters most in |
|---|---|---|
| `warnings` | Anything that went wrong on the way (a 401 from the gateway, an unreachable host, …) | **every mission**: read it first |
| `token_source` | Whether the agent received your token (`inbound`) | A |
| `tools_discovered` / `tools_called` | How many tools the agent found, which ones it called, and what came back | A, B |
| `specialist_configured` / `specialist_consulted` | Whether a document question was handed to the knowledge-base specialist | C |
| `memory_configured` / `memory_hits` / `memory_write` | What the agent remembered and what it stored | D |
| `guardrail_configured` / `guardrail_action` | `passed`, `masked` or `intervened` | F |
| `model` | Which Bedrock model answered | — |

> [!TIP]
> Get into the habit of **expanding the trace after every message**. "The reply looks fine" isn't evidence; `tools_called` and `warnings` are.

## 5. Optional: a terminal with no installs

Every mission can be done entirely in the console. If you prefer the command line, open **AWS CloudShell** (the `>_` icon in the console's top bar). It's already signed in as you and already in your region. Check it with:

```bash
aws sts get-caller-identity   # should show your student-NN user
aws --version                 # the agentcore commands need aws-cli 2.30 or newer
```

`curl` and `jq` are pre-installed, so `curl -s "<chat url>health" | jq` gives you the health check in the terminal.

## 6. How to ask for help (and get it fast)

Before you raise your hand, run the handout's **Self-check** list. Then tell your instructor three things:

1. **The symptom**, quoted exactly (copy it from the error or the trace).
2. **Your hypothesis**: *"I think X, because Y."*
3. **What you already tried**, and what changed when you did.

"It doesn't work" takes ten minutes to diagnose. "The trace says `gateway: 401` and I've updated the runtime's allowed clients but not the gateway's yet" takes ten seconds.

---

**Ready?** Open your first mission from the [mission list](README.md#the-missions).
