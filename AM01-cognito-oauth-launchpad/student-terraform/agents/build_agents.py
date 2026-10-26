#!/usr/bin/env python3
"""Build the AgentCore Runtime deployment zips (direct code deployment).

Called by Terraform through `data "external" "build"` at plan time, so you never
have to run it by hand. Safe to run manually too:

    python3 agents/build_agents.py agents/

For every agent directory (orchestrator/, kb_specialist/) it produces
build/<agent>.zip containing:
  - the agent's own files at the zip root (main.py is the entry point)
  - boto3 + botocore vendored at the zip root (pure-Python wheels, so the same
    zip works on the ARM64 AgentCore Runtime without a Docker build)

It prints a JSON object to stdout (the `external` data source contract) with the
zip path and a sha256 of the *sources* for each agent, and caches aggressively so
repeated `terraform plan` runs cost nothing.
"""
import hashlib
import json
import os
import subprocess
import sys
import zipfile

AGENTS = ["orchestrator", "kb_specialist"]
VENDOR_PKGS = ["boto3>=1.40"]
SKIP_DIRS = {"__pycache__", "tests", ".pytest_cache"}


def log(msg):
    print(msg, file=sys.stderr)


def sha256_tree(path):
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for f in sorted(files):
            if f.endswith((".pyc", ".DS_Store")):
                continue
            p = os.path.join(root, f)
            h.update(os.path.relpath(p, path).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def ensure_vendor(build_dir):
    vendor = os.path.join(build_dir, "vendor")
    marker = os.path.join(vendor, ".vendored-" + "-".join(VENDOR_PKGS).replace(">=", "ge"))
    if os.path.exists(marker) and os.path.isdir(os.path.join(vendor, "boto3")):
        return vendor
    os.makedirs(vendor, exist_ok=True)
    log(f"[build_agents] vendoring {VENDOR_PKGS} into {vendor} (one-time, ~20s)")
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "--target", vendor, "--only-binary=:all:", *VENDOR_PKGS]
    try:
        subprocess.run(cmd, check=True, stdout=sys.stderr, stderr=sys.stderr)
    except Exception as e:  # pip missing? try uv
        log(f"[build_agents] pip failed ({e}); trying uv")
        subprocess.run(["uv", "pip", "install", "--quiet", "--target", vendor, "--only-binary=:all:", *VENDOR_PKGS], check=True, stdout=sys.stderr, stderr=sys.stderr)
    open(marker, "w").close()
    return vendor


def add_tree(zf, base, arc_prefix=""):
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".dist-info") or d.endswith(".dist-info"))
        for d in dirs:
            if d == "__pycache__":
                continue
        for f in sorted(files):
            if f.endswith((".pyc", ".DS_Store")) or f.startswith(".vendored-"):
                continue
            full = os.path.join(root, f)
            arc = os.path.join(arc_prefix, os.path.relpath(full, base))
            zi = zipfile.ZipInfo(arc, date_time=(2024, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = (0o644 << 16)  # rw-r--r-- as required by AgentCore Runtime
            with open(full, "rb") as fh:
                zf.writestr(zi, fh.read())


def build(agents_dir):
    build_dir = os.path.join(agents_dir, "build")
    os.makedirs(build_dir, exist_ok=True)
    result = {}
    vendor = None
    for agent in AGENTS:
        src = os.path.join(agents_dir, agent)
        if not os.path.isdir(src):
            continue
        src_hash = sha256_tree(src)
        zip_path = os.path.join(build_dir, f"{agent}.zip")
        stamp = zip_path + ".sha256"
        up_to_date = os.path.exists(zip_path) and os.path.exists(stamp) and open(stamp).read().strip() == src_hash
        if not up_to_date:
            vendor = vendor or ensure_vendor(build_dir)
            log(f"[build_agents] packaging {agent} -> {zip_path}")
            tmp = zip_path + ".tmp"
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
                add_tree(zf, vendor)
                add_tree(zf, src)
            os.replace(tmp, zip_path)
            with open(stamp, "w") as fh:
                fh.write(src_hash)
        result[f"{agent}_zip"] = os.path.abspath(zip_path)
        result[f"{agent}_sha256"] = src_hash
    return result


if __name__ == "__main__":
    agents_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    # Terraform's external data source sends a JSON query on stdin; we ignore it.
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()
    except Exception:
        pass
    print(json.dumps(build(agents_dir)))
