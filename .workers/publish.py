#!/usr/bin/env python3
"""Publish the .workers/ corpus to the wio status page.

Walks areas/*.md and promises/*.md, and for every exploration with
`status: done` it publishes area -> promise -> rung and records the run as
evidence via the wio CLI grid verbs. Identity/evidence pairing lives in the
spec frontmatter; this script is the enforcement.

This CLI (wio 0.3.0) is the grid model: an "exploration" in the harness spec
maps onto a CLI "rung" (parent promise, parent area). Publication verbs:
  wio areas set   --project --key --spec-path --title --summary
  wio promises set --project --key --area --spec-path --title --statement --invariant-prefix
  wio rungs set    --project --key --promise --spec-path --title --fault-model --workload-path
  wio rungs build-done --project --key --result green|finding --run-id <runId>

Run with a python that has pyyaml (e.g. the harness venv). Requires
WIO_PROJECT_ID in the environment and a logged-in wio CLI.
"""
import os
import sys
import subprocess
import glob

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.environ.get("WIO_PROJECT_ID")
if not PROJECT:
    print("set WIO_PROJECT_ID", file=sys.stderr)
    sys.exit(2)


def fm(path):
    with open(path) as f:
        txt = f.read()
    assert txt.startswith("---"), path
    _, front, _body = txt.split("---", 2)
    return yaml.safe_load(front)


def run(args, check=True):
    print("+", " ".join(args), flush=True)
    r = subprocess.run(args, capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr.strip(), file=sys.stderr)
        # Grid endpoints (harness:*) have returned server errors; do NOT abort the
        # whole publish. The evidence lives in convex as simulation runs; the grid
        # upsert is idempotent, so a later re-run publishes cleanly once the
        # endpoint recovers. Signal failure to the caller instead of raising.
        if check:
            raise _CmdFailed(" ".join(args), r.stderr.strip())
    return r


class _CmdFailed(Exception):
    def __init__(self, cmd, err):
        super().__init__(cmd)
        self.cmd = cmd
        self.err = err


def spec_rel(path):
    return os.path.relpath(path, os.path.join(ROOT, ".."))


def main():
    areas = {}
    for ap in glob.glob(os.path.join(ROOT, "areas", "*.md")):
        if os.path.basename(ap).startswith("_"):
            continue
        a = fm(ap)
        areas[a["key"]] = (a, ap)

    published = []
    failed = []
    for pp in glob.glob(os.path.join(ROOT, "promises", "*.md")):
        if os.path.basename(pp).startswith("_"):
            continue
        p = fm(pp)
        done = [e for e in p.get("explorations", []) if e.get("status") == "done"]
        if not done:
            continue
        area_key = p["area"]
        a, ap = areas[area_key]
        try:
            run(["wio", "areas", "set", "--project", PROJECT, "--key", area_key,
                 "--spec-path", spec_rel(ap), "--title", a["title"],
                 "--summary", a.get("description", "")])
            run(["wio", "promises", "set", "--project", PROJECT, "--key", p["key"],
                 "--area", area_key, "--spec-path", spec_rel(pp), "--title", p["title"],
                 "--statement", p["claim"].strip(),
                 "--invariant-prefix", p.get("invariant_prefix", p["key"])])
            for e in done:
                fault = (e.get("faults") or ["baseline"])
                fault_model = fault[0] if fault else "baseline"
                run(["wio", "rungs", "set", "--project", PROJECT, "--key", e["key"],
                     "--promise", p["key"], "--spec-path", spec_rel(pp),
                     "--title", e["title"], "--fault-model", fault_model,
                     "--workload-path", e.get("workload", "")])
                result = e.get("result")
                cli_result = "finding" if result == "finding" else "green"
                run_id = (e.get("replay") or {}).get("run")
                args = ["wio", "rungs", "build-done", "--project", PROJECT,
                        "--key", e["key"], "--result", cli_result]
                if run_id:
                    args += ["--run-id", str(run_id)]
                run(args)
                published.append((e["key"], cli_result, run_id))
        except _CmdFailed as cf:
            # Grid backend down — leave these explorations published:pending for a
            # later idempotent re-run. Do not lose the rest of the corpus.
            failed.append((p["key"], cf.err))
            print(f"  ! grid publish failed for promise {p['key']}: {cf.err}",
                  file=sys.stderr)

    print("\nPublished:")
    for k, r, rid in published:
        print(f"  {k}: {r} run={rid}")
    if failed:
        print("\nPENDING (grid endpoint error — re-run publish.py when it recovers):")
        for k, err in failed:
            print(f"  {k}: {err}")


if __name__ == "__main__":
    main()
