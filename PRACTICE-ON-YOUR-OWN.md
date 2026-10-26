<sub>[⬅ All missions](README.md)</sub>

# Practise On Your Own: Deploy Any Mission Yourself

After class you can rebuild any mission's **broken** environment in your own AWS account and run it again, as many times as you like. Each mission folder has a `student-terraform/` stack that builds **one** copy of the broken platform for one person. It's the same environment your instructor deployed for the class, with the multi-student loop removed.

```
AM02-gateway-tool-routing/
├── README.md              the mission handout
└── student-terraform/     terraform apply → your own broken OrbitOps, ~5 minutes
```

> [!WARNING]
> **Spoiler alert.** `student-terraform/main.tf` *is* the crime scene: reading it before you attempt the mission gives the game away. Deploy it, then work in the console as if someone else had built it.

## Before you start

These stacks create IAM roles, AgentCore runtimes, a Cognito user pool, Lambda functions, S3 buckets, a **public** Lambda Function URL and (for AM05) a VPC. Use **your own sandbox account**, or get explicit approval to use a shared one.

Your AWS identity needs the permissions in [`iam/self-service-deployer-policy.json`](iam/self-service-deployer-policy.json): Bedrock, Bedrock AgentCore, S3 Vectors, Cognito, Lambda, CloudWatch Logs, S3, IAM role management (including `iam:PassRole`), and EC2/VPC for AM05. Administrator access also works.

Check that **Amazon Bedrock AgentCore**, **Amazon Nova 2 Lite** and **Titan Text Embeddings V2** are available to your account in **`us-west-2`**, the region every stack defaults to.

## 1. Install the tools

| Tool | Version | Install |
|---|---|---|
| **Terraform** | ≥ 1.6 | macOS: `brew tap hashicorp/tap && brew install hashicorp/tap/terraform` · Windows: `winget install HashiCorp.Terraform` · Linux / manual: <https://developer.hashicorp.com/terraform/install> |
| **AWS CLI v2** | ≥ 2.30 | macOS: `brew install awscli` · Windows/Linux: <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html> |
| **Python** | ≥ 3.10, with `pip` and `boto3` | `python3 -m pip install boto3`. The agents are zipped at `terraform plan` time. |
| **jq** | any | used by the handouts' CLI snippets |

Then connect the CLI to your account:

```bash
aws configure            # access key, secret, region us-west-2, output json
# or, with IAM Identity Center:  aws configure sso   (then export AWS_PROFILE=<profile>)
aws sts get-caller-identity
```

## 2. Pick your id

Every resource is named `orbit-<your id>-amNN-…`. Choose **3–12 lowercase letters or digits, no hyphens**, that nobody else in the account would use, e.g. `jsmith42`.

## 3. Deploy, practise, destroy

```bash
cd AM02-gateway-tool-routing/student-terraform
terraform init
terraform apply -var 'student_id=jsmith42'     # about 5 minutes
terraform output                                # your chat URL and every value the handout mentions
```

Now open the mission's `README.md` and play it exactly as in class. Wherever the handout says *"your instructor gives you…"*, use `terraform output` instead.

> [!TIP]
> Put `student_id = "jsmith42"` in a `terraform.tfvars` file next to the `.tf` files (it's git-ignored) and drop the `-var`.

When you're done (and **don't leave it running**: the chat URL is public and AM05's endpoints bill by the hour):

1. **Delete what you built by hand first.** The knowledge base, specialist runtime, memory, VPC endpoints, guardrail or app client you created during the mission are *not* tracked by Terraform. Some of them block the destroy.
2. Then:

```bash
terraform destroy -var 'student_id=jsmith42'
```

Want to try again? Run `terraform apply` again for a fresh broken environment.

## What it costs

Everything is pay-per-use and close to **$0 when idle**. A full practice run of a mission typically costs **cents**.

| Mission | Watch out for |
|---|---|
| All | AgentCore Runtime active seconds; Cognito M2M token requests (~$0.00225 each, cached for an hour); Nova 2 Lite tokens |
| AM03 / AM05 | S3 Vectors storage and Titan embeddings: cents. **Never** let a console wizard create an OpenSearch Serverless collection (~$350+/month). |
| AM05 | Interface endpoints: ~$0.01 per hour each, per Availability Zone. Uses one VPC (default quota: 5 per region). |
| AM06 | Guardrail evaluations: cents. |

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Missing required argument` for `student_id` | no `-var` and no `terraform.tfvars` | Set it. There's deliberately no default. |
| `AccessDeniedException` from `bedrock-agentcore`, `bedrock` or `s3vectors` | your identity lacks permissions | Attach the deployer policy above (or ask your account admin). |
| `data.external.build` fails | no `python3` / `pip` / `boto3` | Install Python 3.10+, then `python3 -m pip install boto3`. |
| Cognito domain already exists | the same id was used before in this region | Destroy the old stack, or pick another id. |
| Runtime `CREATE_FAILED` | IAM role not propagated yet | Run `terraform apply` again. |
| `Invalid choice … bedrock-agentcore-control` | AWS CLI too old | Upgrade to 2.30+, use CloudShell, or use the console. |
| `VpcLimitExceeded` (AM05) | already 5 VPCs in the region | Delete one or request a quota increase. |
| `destroy` fails on the vector bucket or the VPC | something you created by hand still uses it | Delete your hand-made resources (step 3.1), then destroy again. |

## State is local and yours

There's no remote backend: Terraform keeps its state in `terraform.tfstate` next to the `.tf` files. Don't delete that file before you run `terraform destroy`, or Terraform loses track of what it built.
