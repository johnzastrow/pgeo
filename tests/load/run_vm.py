"""Production validation: the same k6 session and ramp against VM 120 through the real HTTPS path.

    python3 tests/load/run_vm.py [--engines pelias,pgeo] [--base https://geocoder.example.org]
                                 [--ssh jcz@192.0.2.20] [--quick]

Unlike run_matrix*.py this changes nothing on the target: the VM runs as deployed (4 vCPU,
14 GB, both engines). Engines are tested one at a time; the other stays idle. The edge's
per-client rate limits must be raised for the test window (the load generator is one IP):

    cd infra/ansible && ansible-playbook site.yml --tags edge -e edge_rate_search=2000 -e edge_rate_autocomplete=5000
    ... run this script ...
    cd infra/ansible && ansible-playbook site.yml --tags edge        # restore the normal limits

Resources are sampled on the VM over SSH (docker stats). Results: data/loadtest/<run>-vm/,
same format as the workstation runs, so tests/load/report.py and the study report read them.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from datetime import datetime

import run_matrix as rm

ENGINES = {"pelias": "", "pgeo": "/pgeo"}  # path prefix under the site
CONTAINERS = {
    "pelias": ["pelias_maine_elasticsearch", "pelias_maine_api", "pelias_maine_libpostal", "pelias_maine_placeholder",
               "pelias_maine_pip", "pelias_maine_interpolation"],
    "pgeo": ["pgeo_db", "pgeo_rest"],
}  # fmt: skip


class RemoteSampler(threading.Thread):
    """docker stats on the VM every ~2 s (same sample format as run_matrix.Sampler)."""

    def __init__(self, ssh: str, names: list[str]) -> None:
        super().__init__(daemon=True)
        self.ssh, self.names = ssh, names
        self.samples: list[dict] = []
        self._stop = threading.Event()

    def run(self) -> None:
        # the admin account is not in the VM's docker group: sudo -n (passwordless, as Ansible uses)
        cmd = ["ssh", "-o", "BatchMode=yes", self.ssh, "sudo", "-n", "docker", "stats", "--no-stream", "--format",
               "'{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}'", *self.names]  # fmt: skip
        while not self._stop.is_set():
            r = subprocess.run(cmd, text=True, capture_output=True, timeout=30, check=False)
            t = time.time()
            for line in r.stdout.splitlines():
                try:
                    name, cpu, mem = line.split("|")
                except ValueError:
                    continue
                used = mem.split("/")[0].strip()
                num = float(used[:-3]) if used[-3:] in ("GiB", "MiB", "KiB") else 0.0
                mbv = num * {"GiB": 1024, "MiB": 1, "KiB": 1 / 1024}.get(used[-3:], 0)
                self.samples.append({"t": t, "svc": name.split("_", 1)[-1] if name.startswith("pelias_maine_") else name,
                                     "cpu": float(cpu.rstrip("%") or 0), "mem_mb": mbv})  # fmt: skip
            self._stop.wait(2)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=40)


def health(ssh: str, names: list[str]) -> dict:
    fmt = "'{{.Name}} {{.State.Status}} {{.State.OOMKilled}} {{.RestartCount}}'"
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", ssh, "sudo", "-n", "docker", "inspect", "-f", fmt, *names],
                       text=True, capture_output=True, timeout=30, check=False)  # fmt: skip
    out = {}
    for line in r.stdout.splitlines():
        name, status, oom, restarts = (line.split() + ["?"] * 4)[:4]
        out[name] = {"status": status, "oom": oom == "true", "restarts": int(restarts) if restarts.isdigit() else -1}
    return out


def summarize(samples: list[dict], names: list[str]) -> dict:
    saved = rm.SERVICES
    rm.SERVICES = sorted({s["svc"] for s in samples})
    try:
        return rm.summarize_resources(samples)
    finally:
        rm.SERVICES = saved


def died(h: dict, base: dict) -> bool:
    # restart counts are cumulative on the VM: compare with the counts before the run
    return any(v["status"] != "running" or v["oom"] or v["restarts"] > base.get(k, {}).get("restarts", 0)
               for k, v in h.items())  # fmt: skip


def run_engine(engine: str, base: str, ssh: str, out_dir, quick: bool, label: str = "", size: dict | None = None) -> dict:
    rid = f"vm-{engine}" + (f"-{label}" if label else "")
    print(f"\n=== {rid} {base}{ENGINES[engine]}", flush=True)
    rm.API = base + ENGINES[engine]
    names = CONTAINERS[engine]
    base_health = health(ssh, names)
    rm.run_k6(2, "30s", "5s", out_dir / rid / "warmup.json")
    size = size or {"cpus": 4, "cpulimit": 4, "memory_gb": 14, "host": "VM 120"}
    result = {"id": rid, "engine": engine, "base_config": label or "VM120", "dataset": None, "config": size,
              "budget_gb": size["memory_gb"], "runs": {}}  # fmt: skip
    val_dur, val_warm = ("60s", "15s") if quick else ("180s", "45s")
    s = RemoteSampler(ssh, names)
    s.start()
    summ = rm.run_k6(3, val_dur, val_warm, out_dir / rid / "validate-3.json")
    s.stop()
    h = health(ssh, names)
    v = rm.evaluate(summ) | {"resources": summarize(s.samples, names), "health": h, "died": died(h, base_health)}
    v["pass"] = v["pass"] and not v["died"]
    result["runs"]["validate"] = v
    print(f"  validate@3: pass={v['pass']} err={v['error_rate']} worst={v['worst_p95_ratio']} rps={v['req_rate']}", flush=True)
    ramp, limit = [], 0
    step_dur, step_warm = ("30s", "10s") if quick else ("60s", "15s")
    for n in rm.RAMP:
        s = RemoteSampler(ssh, names)
        s.start()
        summ = rm.run_k6(n, step_dur, step_warm, out_dir / rid / f"ramp-{n:03d}.json")
        s.stop()
        e = rm.evaluate(summ)
        h = health(ssh, names)
        step = e | {"vus": n, "resources": summarize(s.samples, names), "health": h, "died": died(h, base_health)}
        ramp.append(step)
        limit = n if e["pass"] else limit
        print(f"  ramp@{n}: pass={e['pass']} err={e['error_rate']} worst={e['worst_p95_ratio']} rps={e['req_rate']}"
              f"{' DIED' if step['died'] else ''}", flush=True)  # fmt: skip
        if step["died"] or e["broken"]:
            break
    result["runs"]["ramp"] = ramp
    result["limit_users"] = limit
    result["breaking_users"] = ramp[-1]["vus"] if ramp and (ramp[-1]["broken"] or ramp[-1]["died"]) else None
    (out_dir / f"{rid}.json").write_text(json.dumps(result, indent=1))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="pelias,pgeo")
    ap.add_argument("--base", default="https://geocoder.example.org")
    ap.add_argument("--ssh", default="jcz@192.0.2.20")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--label", default="", help="run label, e.g. floor-pgeo-1c-0.5-1536mb")
    ap.add_argument("--cpus", type=int, default=4)
    ap.add_argument("--cpulimit", type=float, default=0)
    ap.add_argument("--memory-gb", type=float, default=14)
    ap.add_argument("--host", default="VM 120")
    a = ap.parse_args()
    out_dir = rm.ROOT / "data" / "loadtest" / (datetime.now().astimezone().strftime("%Y%m%d-%H%M") + "-vm")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"run {out_dir.name}", flush=True)
    for e in a.engines.split(","):
        if e not in ENGINES:
            ap.error(f"unknown engine {e}")
        size = {"cpus": a.cpus, "cpulimit": a.cpulimit or a.cpus, "memory_gb": a.memory_gb, "host": a.host}
        run_engine(e, a.base.rstrip("/"), a.ssh, out_dir, a.quick, a.label, size)
    print(f"done: {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
