#!/usr/bin/env python3
"""Start a Bedrock Knowledge Base ingestion job and wait for it (used by the solution stacks).

usage: start_ingestion.py <knowledge_base_id> <data_source_id> <region>
"""
import sys
import time

import boto3

kb_id, ds_id, region = sys.argv[1], sys.argv[2], sys.argv[3]
client = boto3.client("bedrock-agent", region_name=region)
job = client.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)["ingestionJob"]
job_id = job["ingestionJobId"]
print(f"[ingestion] started job {job_id} for KB {kb_id} / data source {ds_id}")
deadline = time.time() + 900
while time.time() < deadline:
    j = client.get_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id, ingestionJobId=job_id)["ingestionJob"]
    status = j["status"]
    if status in ("COMPLETE", "FAILED", "STOPPED"):
        print(f"[ingestion] {status}: {j.get('statistics', {})} {j.get('failureReasons', '')}")
        sys.exit(0 if status == "COMPLETE" else 1)
    time.sleep(10)
print("[ingestion] timed out waiting for the ingestion job")
sys.exit(1)
