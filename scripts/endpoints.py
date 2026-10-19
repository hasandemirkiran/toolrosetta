"""Self-deployed endpoint lifecycle on Gemini Enterprise Agent Platform (formerly Vertex AI).

  python scripts/endpoints.py list                      # every endpoint in the configured regions, uptime, $ so far
  python scripts/endpoints.py deploy qwen3.8-27b        # prints the gcloud command; --execute runs it
  python scripts/endpoints.py sync                      # writes endpoint ids + dedicated DNS into configs/models.yaml
  python scripts/endpoints.py undeploy qwen3.8-27b --yes  # undeploy + delete endpoint (stops billing)
  python scripts/endpoints.py undeploy --all --yes

REST calls use ADC (google-auth) against the stable aiplatform.googleapis.com API, so they do
not depend on SDK names that changed in the rebrand. `deploy` shells out to gcloud; the
console's "Equivalent code" button on the deploy panel is the authoritative reference if a
flag name differs — paste it over `GCLOUD_TEMPLATE` below.

The budget risk here is not tokens: three RTX PRO 6000 endpoints left running for a week
cost ≈ $2.5k. `list` shows running cost; undeploy between runs.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
MODELS_YAML = ROOT / "configs" / "models.yaml"

# gcloud deploy template (flag names verified against Google Cloud SDK 571.0.0, 2026-09-04:
# Hugging Face repos go in --model=, there is no --model-display-name). Verify machine specs with:
#   gcloud ai model-garden models list-deployment-config --model=Qwen/Qwen3.8-27B-FP8
GCLOUD_TEMPLATE = (
    "gcloud ai model-garden models deploy "
    "--model={hf_repo} "
    "--region={location} "
    "--machine-type={machine_type} "
    "--accelerator-type={accelerator_type} "
    "--accelerator-count={accelerator_count} "
    "--container-image-uri={container_image_uri} "
    "--endpoint-display-name={display_name} "
    "--use-dedicated-endpoint "
    "--container-args={container_args} "
    "--accept-eula "
    "--project={project} "
    "--billing-project={project}"  # gcloud model-garden calls need a quota project even with ADC
)
# Serving image pinned to the one `list-deployment-config` reported for Qwen3.8-27B-FP8 and Nemotron 3.5
# on 2026-09-04; passing it explicitly is also what lets unverified HF repos (TS-Guard) deploy at all.
VLLM_IMAGE = "us-docker.pkg.dev/vertex-ai/vertex-vision-model-garden-dockers/pytorch-vllm-serve:20260713_0916_RC01"
DEFAULT_SERVING = {"machine_type": "g4-standard-48", "accelerator_type": "NVIDIA_RTX_PRO_6000", "accelerator_count": 1, "max_model_len": 32768, "container_image_uri": VLLM_IMAGE}


def _cfg() -> dict:
    return yaml.safe_load(MODELS_YAML.read_text(encoding="utf-8"))


def _project(cfg: dict) -> str:
    p = os.environ.get("GOOGLE_CLOUD_PROJECT") or cfg.get("project", "")
    if not p or str(p).startswith("${"):
        sys.exit("export GOOGLE_CLOUD_PROJECT=<project-id>")
    return p


def _token() -> str:
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _api(method: str, region: str, path: str, **kw) -> dict:
    url = f"https://{region}-aiplatform.googleapis.com/v1/{path}"
    r = requests.request(method, url, headers={"Authorization": f"Bearer {_token()}"}, timeout=120, **kw)
    if r.status_code >= 400:
        raise SystemExit(f"{method} {url} → {r.status_code}: {r.text[:400]}")
    return r.json() if r.text else {}


def _self_deployed(cfg: dict) -> dict[str, dict]:
    return {g["id"]: g for g in cfg["guards"] if g.get("provider") == "vertex_endpoint"}


def _regions(cfg: dict) -> list[str]:
    return sorted({g.get("location") for g in _self_deployed(cfg).values() if g.get("location")})


def display_name(guard: dict) -> str:
    return f"ai-{guard['id']}"


def cmd_list(a):
    cfg = _cfg()
    project = _project(cfg)
    guards = _self_deployed(cfg)
    rate_by_name = {display_name(g): float(g.get("usd_per_hour", 0)) for g in guards.values()}
    now = dt.datetime.now(dt.timezone.utc)
    total = 0.0
    rows = []
    for region in _regions(cfg):
        data = _api("GET", region, f"projects/{project}/locations/{region}/endpoints")
        for ep in data.get("endpoints", []):
            eid = ep["name"].rsplit("/", 1)[-1]
            for dm in ep.get("deployedModels", []):
                created = dt.datetime.fromisoformat(dm["createTime"].replace("Z", "+00:00"))
                hours = (now - created).total_seconds() / 3600
                spec = dm.get("dedicatedResources", {}).get("machineSpec", {})
                rate = rate_by_name.get(ep.get("displayName", ""), 0.0)
                usd = hours * rate
                total += usd
                rows.append({"region": region, "endpoint_id": eid, "display_name": ep.get("displayName"), "deployed_model_id": dm["id"], "machine": f"{spec.get('machineType')} / {spec.get('acceleratorType')} ×{spec.get('acceleratorCount')}", "uptime_h": round(hours, 1), "usd_per_hour": rate, "usd_so_far": round(usd, 2), "dedicated_dns": ep.get("dedicatedEndpointDns")})
    if not rows:
        print("no deployed models in", ", ".join(_regions(cfg)) or "(no regions configured)")
        return
    for r in rows:
        print(f"{r['display_name']:28s} {r['region']:14s} id={r['endpoint_id']:20s} {r['machine']:48s} up {r['uptime_h']:6.1f} h  ≈ ${r['usd_so_far']:8,.2f}")
    print(f"\nrunning total ≈ ${total:,.2f}  (rates from configs/models.yaml; billing continues until `undeploy`)")


def _wait(region: str, op_name: str, *, every: int = 20, timeout_s: int = 3600) -> dict:
    """Poll a long-running operation (`projects/.../operations/...`) until done."""
    import time

    t0 = time.time()
    while True:
        op = _api("GET", region, op_name)
        if op.get("done"):
            if op.get("error"):
                raise SystemExit(f"operation {op_name} failed: {json.dumps(op['error'])[:600]}")
            return op.get("response", {})
        if time.time() - t0 > timeout_s:
            raise SystemExit(f"operation {op_name} still running after {timeout_s}s")
        print(f"  … {op_name.rsplit('/', 1)[-1]} running ({int(time.time() - t0)}s)", flush=True)
        time.sleep(every)


def _deploy_custom(g: dict, serving: dict, project: str, *, execute: bool) -> None:
    """Hugging Face repos that Model Garden has not "verified" (TS-Guard) are refused by
    `gcloud ai model-garden models deploy`. Register the same vLLM serving image as a custom
    Model resource with the standard OpenAI-compatible vLLM server pointed at the HF repo,
    create a *dedicated* endpoint named ai-<id>, and deploy on the same machine spec."""
    region = g["location"]
    name = display_name(g)
    args = [
        "--host=0.0.0.0", "--port=8080", f"--model={g['hf_repo']}", f"--served-model-name={g['model']}",
        f"--max-model-len={serving['max_model_len']}", "--gpu-memory-utilization=0.90", "--enable-chunked-prefill",
    ]
    if g.get("hf_revision"):
        args.append(f"--revision={g['hf_revision']}")
    # Pre-quantized FP8 checkpoints (repo name ends in -FP8) carry quantization_config; vLLM
    # auto-detects it and --quantization=fp8 is for on-the-fly quantization of bf16 weights.
    if serving.get("dtype") == "fp8" and not g["hf_repo"].upper().endswith("FP8"):
        args.append("--quantization=fp8")
    elif serving.get("dtype") and serving["dtype"] != "fp8":
        args.append(f"--dtype={serving['dtype']}")
    args += list(serving.get("extra_args") or [])
    env = [{"name": "MODEL_ID", "value": g["hf_repo"]}, {"name": "DEPLOY_SOURCE", "value": "action-invariance/endpoints.py"}]
    if os.environ.get("HF_TOKEN"):
        env.append({"name": "HF_TOKEN", "value": os.environ["HF_TOKEN"]})
    model_body = {"model": {"displayName": name, "containerSpec": {"imageUri": serving["container_image_uri"], "command": ["python", "-m", "vllm.entrypoints.openai.api_server"], "args": args, "env": env, "ports": [{"containerPort": 8080}], "predictRoute": "/v1/chat/completions", "healthRoute": "/health"}}}
    endpoint_body = {"displayName": name, "dedicatedEndpointEnabled": True}
    deploy_body = {"deployedModel": {"displayName": name, "dedicatedResources": {"machineSpec": {"machineType": serving["machine_type"], "acceleratorType": serving["accelerator_type"], "acceleratorCount": serving["accelerator_count"]}, "minReplicaCount": 1, "maxReplicaCount": 1}}, "trafficSplit": {"0": 100}}
    print(f"# custom-container deploy of {g['hf_repo']} in {region} (REST):")
    redacted = json.loads(json.dumps(model_body))
    redacted["model"]["containerSpec"]["env"] = [e for e in redacted["model"]["containerSpec"]["env"] if e["name"] != "HF_TOKEN"] + ([{"name": "HF_TOKEN", "value": "<redacted>"}] if os.environ.get("HF_TOKEN") else [])
    print("POST models:upload", json.dumps(redacted))
    print("POST endpoints", json.dumps(endpoint_body))
    print("POST endpoints/<id>:deployModel", json.dumps(deploy_body))
    if not execute:
        return
    parent = f"projects/{project}/locations/{region}"
    # Reuse a Model only if it is *our* spec (same image + command); Model Garden's own deploys
    # register Models under other names/specs and must not be picked up here.
    existing = [m for m in _api("GET", region, f"{parent}/models").get("models", []) if m.get("displayName") == name and (m.get("containerSpec") or {}).get("imageUri") == serving["container_image_uri"] and (m.get("containerSpec") or {}).get("command") == model_body["model"]["containerSpec"]["command"] and (m.get("containerSpec") or {}).get("args") == args]
    if existing:
        model_name = existing[0]["name"]
        print(f"reusing Model {model_name}")
    else:
        print("uploading Model …", flush=True)
        op = _api("POST", region, f"{parent}/models:upload", json=model_body)
        model_name = _wait(region, op["name"])["model"]
        print(f"Model {model_name}")
    eps = [e for e in _api("GET", region, f"{parent}/endpoints").get("endpoints", []) if e.get("displayName") == name]
    if eps:
        ep_name = eps[0]["name"]
        print(f"reusing Endpoint {ep_name}")
    else:
        op = _api("POST", region, f"{parent}/endpoints", json=endpoint_body)
        ep_name = _wait(region, op["name"])["name"] if "name" in op and "/operations/" in op["name"] else op["name"]
        print(f"Endpoint {ep_name}")
    deploy_body["deployedModel"]["model"] = model_name
    print("deploying (10–20 min: image pull + weights download) …", flush=True)
    op = _api("POST", region, f"{ep_name}:deployModel", json=deploy_body)
    _wait(region, op["name"])
    print("\ndeployed. now: python scripts/endpoints.py sync")


def cmd_deploy(a):
    cfg = _cfg()
    project = _project(cfg)
    g = _self_deployed(cfg).get(a.guard)
    if not g:
        sys.exit(f"{a.guard} is not a vertex_endpoint guard in models.yaml")
    serving = {**DEFAULT_SERVING, **(g.get("serving") or {})}
    # NOTE (2026-09-04): on the Model Garden path --container-args REPLACES the image's launch
    # command (the launcher then execs "--max-model-len=32768" and dies), so every guard now
    # uses deploy_mode: custom_container, which passes an explicit vLLM command instead.
    if g.get("deploy_mode") == "custom_container":
        _deploy_custom(g, serving, project, execute=a.execute)
        if not a.execute:
            print("\n(dry run — add --execute to run it)")
        return
    container_args = f"--max-model-len={serving['max_model_len']}"
    if serving.get("dtype"):
        container_args += f",--dtype={serving['dtype']}" if serving["dtype"] != "fp8" else ",--quantization=fp8"
    cmd = GCLOUD_TEMPLATE.format(hf_repo=g["hf_repo"], location=g["location"], machine_type=serving["machine_type"], accelerator_type=serving["accelerator_type"], accelerator_count=serving["accelerator_count"], container_image_uri=serving["container_image_uri"], display_name=display_name(g), container_args=shlex.quote(container_args), project=project)
    if g.get("hf_revision"):
        print(f"# note: pin revision {g['hf_revision']} (append @{g['hf_revision']} to the HF model id if gcloud accepts it)")
    if os.environ.get("HF_TOKEN"):
        cmd += " --hugging-face-access-token=$HF_TOKEN"
    print(cmd)
    if a.execute:
        print("\nrunning…", flush=True)
        rc = subprocess.call(cmd.replace("$HF_TOKEN", os.environ.get("HF_TOKEN", "")), shell=True)
        if rc == 0:
            print("\ndeployed. now: python scripts/endpoints.py sync")
        sys.exit(rc)
    print("\n(dry run — add --execute to run it, or use the console's 'Equivalent code' if a flag differs)")


def cmd_sync(a):
    cfg = _cfg()
    project = _project(cfg)
    guards = _self_deployed(cfg)
    text = MODELS_YAML.read_text(encoding="utf-8")
    found = {}
    for region in _regions(cfg):
        data = _api("GET", region, f"projects/{project}/locations/{region}/endpoints")
        for ep in data.get("endpoints", []):
            for gid, g in guards.items():
                if ep.get("displayName") == display_name(g) and ep.get("deployedModels"):
                    found[gid] = {"endpoint_id": ep["name"].rsplit("/", 1)[-1], "location": region, "dedicated_dns": ep.get("dedicatedEndpointDns", "")}
    # Rewrite the guard blocks in place (string edits keep comments intact).
    import re

    for gid, info in found.items():
        block = re.search(rf"(  - id: {re.escape(gid)}\n(?:    .*\n)+)", text)
        if not block:
            continue
        b = block.group(1)
        b2 = re.sub(r'    endpoint_id: "[^"]*"', f'    endpoint_id: "{info["endpoint_id"]}"', b)
        if "dedicated_dns:" in b2:
            b2 = re.sub(r'    dedicated_dns: "[^"]*"', f'    dedicated_dns: "{info["dedicated_dns"]}"', b2)
        else:
            b2 = b2.replace(f'    endpoint_id: "{info["endpoint_id"]}"', f'    endpoint_id: "{info["endpoint_id"]}"\n    dedicated_dns: "{info["dedicated_dns"]}"')
        text = text.replace(b, b2)
        print(f"{gid}: endpoint_id={info['endpoint_id']} region={info['location']} dns={info['dedicated_dns'] or '-'}")
    missing = [gid for gid in guards if gid not in found]
    if missing:
        print("not deployed (no endpoint named ai-<id> with a deployed model):", ", ".join(missing))
    MODELS_YAML.write_text(text, encoding="utf-8")


def cmd_undeploy(a):
    cfg = _cfg()
    project = _project(cfg)
    guards = _self_deployed(cfg)
    targets = list(guards) if a.all else [a.guard]
    if not a.yes:
        sys.exit("refusing without --yes (this deletes the endpoint; the Model resource is kept and is free)")
    for gid in targets:
        g = guards[gid]
        region = g["location"]
        data = _api("GET", region, f"projects/{project}/locations/{region}/endpoints")
        for ep in data.get("endpoints", []):
            if ep.get("displayName") != display_name(g):
                continue
            for dm in ep.get("deployedModels", []):
                print(f"{gid}: undeploying {dm['id']} …", flush=True)
                _api("POST", region, f"{ep['name']}:undeployModel", json={"deployedModelId": dm["id"]})
            print(f"{gid}: deleting endpoint {ep['name'].rsplit('/', 1)[-1]}")
            _api("DELETE", region, ep["name"])
    print("done — record the hours with: action-invariance cost --add-hours <guard> <hours> <usd_per_hour>")


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("list").set_defaults(fn=cmd_list)
    s = sp.add_parser("deploy"); s.add_argument("guard"); s.add_argument("--execute", action="store_true"); s.set_defaults(fn=cmd_deploy)
    sp.add_parser("sync").set_defaults(fn=cmd_sync)
    s = sp.add_parser("undeploy"); s.add_argument("guard", nargs="?"); s.add_argument("--all", action="store_true"); s.add_argument("--yes", action="store_true"); s.set_defaults(fn=cmd_undeploy)
    a = ap.parse_args()
    if a.cmd == "undeploy" and not a.all and not a.guard:
        ap.error("guard or --all")
    a.fn(a)


if __name__ == "__main__":
    main()
