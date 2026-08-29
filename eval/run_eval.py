"""Evaluation harness for the Competitive Analysis Agent.

Runs every case in eval/dataset.py through the real pipeline and measures:

- success   : did the flow do the right thing (produced a plan / failed
              gracefully / wrote to Salesforce when expected)?
- accuracy  : content correctness (fraction of the 11 plan fields populated;
              or, for fictitious companies, whether it failed at the right stage)?
- latency   : per-phase and total wall-clock time.
- output    : the actual plan (or the error) each case produced.

It then writes a self-contained visual dashboard (eval/results/dashboard.html)
and the raw results (eval/results/results.json), and prints a summary.

Salesforce hygiene: the harness snapshots which Account records already exist
BEFORE the run, then afterward deletes every AccountPlan it created and only
those Accounts that did NOT pre-exist. Real accounts (Salesforce, Schlumberger)
are protected; test-created ones (e.g. a throwaway Databricks Account) are removed.

Run from anywhere:
    .venv/bin/python eval/run_eval.py
"""

import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

# Run from the project root so config's load_dotenv(".env.demo") resolves and
# `import pipeline` works regardless of the caller's cwd.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import pipeline  # noqa: E402
from config import (  # noqa: E402
    MCP_HTTP_URL,
    SF_API_VERSION,
    SF_DOMAIN,
    SF_PASSWORD,
    SF_SECURITY_TOKEN,
    SF_USERNAME,
)
from eval.dataset import CASES, EXPECTED_PLAN_FIELDS  # noqa: E402
from tools.salesforce_client import check_mcp_health  # noqa: E402

RESULTS_DIR = os.path.join(ROOT, "eval", "results")


# --------------------------------------------------------------------------- #
# Salesforce helpers (snapshot + cleanup)                                      #
# --------------------------------------------------------------------------- #
def sf_connect():
    """Open a Salesforce session for snapshot/cleanup, or return None if unavailable."""
    if not SF_USERNAME or not SF_PASSWORD:
        return None
    try:
        from simple_salesforce import Salesforce

        return Salesforce(
            username=SF_USERNAME,
            password=SF_PASSWORD,
            security_token=SF_SECURITY_TOKEN,
            domain=SF_DOMAIN,
            version=SF_API_VERSION,
        )
    except Exception as exc:
        print(f"[eval] Salesforce connect failed ({exc}); SF steps will be skipped.")
        return None


def snapshot_account_ids(sf, company):
    """Return the set of existing Account Ids named `company` (pre-run snapshot)."""
    safe = company.replace("\\", "\\\\").replace("'", "\\'")
    try:
        res = sf.query(f"SELECT Id FROM Account WHERE Name = '{safe}'")
        return {r["Id"] for r in res.get("records", [])}
    except Exception as exc:
        print(f"[eval] snapshot query failed for '{company}': {exc}")
        return set()


def cleanup(sf, created_plans, created_accounts, protected_ids):
    """Delete test-run records. Plans (children) first, then only NEW accounts.

    - created_plans:    list of (case_id, account_plan_id) we created this run.
    - created_accounts: list of (case_id, account_id) touched by our writes.
    - protected_ids:    Account Ids that existed BEFORE the run (never deleted).
    """
    log = {
        "deleted_plans": [],
        "deleted_accounts": [],
        "protected_accounts": [],
        "errors": [],
    }
    if not sf:
        log["errors"].append("No Salesforce connection; nothing to clean up.")
        return log

    for case_id, plan_id in created_plans:
        try:
            sf.AccountPlan.delete(plan_id)
            log["deleted_plans"].append({"case": case_id, "id": plan_id})
        except Exception as exc:
            log["errors"].append(f"AccountPlan {plan_id}: {exc}")

    seen = set()
    for case_id, account_id in created_accounts:
        if account_id in seen:
            continue
        seen.add(account_id)
        if account_id in protected_ids:
            log["protected_accounts"].append({"case": case_id, "id": account_id})
            continue
        try:
            sf.Account.delete(account_id)
            log["deleted_accounts"].append({"case": case_id, "id": account_id})
        except Exception as exc:
            log["errors"].append(f"Account {account_id}: {exc}")

    return log


# --------------------------------------------------------------------------- #
# HTTP MCP server (booted only if the dataset has an http case)                #
# --------------------------------------------------------------------------- #
class HttpMcpServer:
    """Boots `mcp_transport=http python mcp_server.py` and tears it down after."""

    def __init__(self):
        self.proc = None
        self.log_path = os.path.join(RESULTS_DIR, "mcp_http_server.log")

    def start(self, timeout_s=20):
        env = dict(os.environ, mcp_transport="http")
        logf = open(self.log_path, "w")
        self.proc = subprocess.Popen(
            [sys.executable, "mcp_server.py"],
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(0.5)
            if self.proc.poll() is not None:
                return False  # server died on startup
            if check_mcp_health("http").get("ok"):
                return True
        return False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()


# --------------------------------------------------------------------------- #
# Running + scoring a single case                                              #
# --------------------------------------------------------------------------- #
def run_analyze(company, use_memory):
    """Drain analyze_stream, timestamping each step. Returns (steps, terminal, elapsed)."""
    steps = []
    terminal = None
    t_start = time.perf_counter()
    t_prev = t_start
    for event in pipeline.analyze_stream(company, use_memory=use_memory):
        now = time.perf_counter()
        etype = event.get("type")
        if etype == "step":
            steps.append(
                {
                    "message": event["message"],
                    "t": round(now - t_start, 2),
                    "dt": round(now - t_prev, 2),
                }
            )
            t_prev = now
        elif etype == "result":
            terminal = ("result", event)
        elif etype == "error":
            terminal = ("error", event)
    elapsed = round(time.perf_counter() - t_start, 2)
    return steps, terminal, elapsed


def score_analyze(case, terminal):
    """Return (analyze_success, accuracy, accuracy_label, detail) for the analyze phase."""
    expect = case["expect"]

    if expect == "plan":
        if not terminal or terminal[0] != "result":
            reason = terminal[1].get("message") if terminal else "no terminal event"
            return False, 0.0, f"Expected a plan; analyze failed ({reason}).", {}
        plan = terminal[1].get("plan") or {}
        missing = [f for f in EXPECTED_PLAN_FIELDS if not plan.get(f)]
        extra = [k for k in plan if k not in EXPECTED_PLAN_FIELDS]
        present = len(EXPECTED_PLAN_FIELDS) - len(missing)
        accuracy = present / len(EXPECTED_PLAN_FIELDS)
        detail = {
            "fields_present": present,
            "fields_total": len(EXPECTED_PLAN_FIELDS),
            "missing": missing,
            "extra": extra,
        }
        if not missing and not extra:
            label = f"All {len(EXPECTED_PLAN_FIELDS)} fields populated."
        else:
            label = f"{present}/{len(EXPECTED_PLAN_FIELDS)} fields populated."
            if extra:
                label += f" Unexpected fields: {extra}."
        return True, accuracy, label, detail

    if expect == "discover_fail":
        if terminal and terminal[0] == "error":
            stage = terminal[1].get("stage")
            if stage == "discover":
                return True, 1.0, "Failed gracefully at Agent 1 (discover), as expected.", {"stage": stage}
            return True, 0.0, f"Failed, but at the wrong stage ('{stage}').", {"stage": stage}
        # A fictitious company that produced a plan means Agent 1 hallucinated.
        return False, 0.0, "Fictitious company unexpectedly produced a plan (hallucination).", {}

    return False, 0.0, f"Unknown expectation '{expect}'.", {}


def run_submit(case, plan, http_available):
    """Run Agent 5 via MCP for a case that produced a plan."""
    transport = case["transport"]
    if transport == "http" and not http_available:
        return {
            "status": "SKIPPED",
            "message": "http MCP server was not available; submit skipped.",
            "elapsed": None,
            "details": {},
        }
    t0 = time.perf_counter()
    res = dict(pipeline.submit(case["company"], plan, transport=transport))
    res["elapsed"] = round(time.perf_counter() - t0, 2)
    return res


def extract_output(terminal):
    """Shape the terminal event into a display-friendly output block."""
    if not terminal:
        return {"kind": "none"}
    if terminal[0] == "result":
        ev = terminal[1]
        return {"kind": "plan", "competitors": ev.get("competitors", []), "plan": ev.get("plan", {})}
    ev = terminal[1]
    return {"kind": "error", "stage": ev.get("stage"), "message": ev.get("message")}


# --------------------------------------------------------------------------- #
# Main                                                                          #
# --------------------------------------------------------------------------- #
def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    started = time.perf_counter()
    started_iso = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    sf = sf_connect()

    # Snapshot pre-existing Accounts for every company we might write to, so
    # cleanup can distinguish "found" (protect) from "created" (delete).
    submit_companies = {c["company"] for c in CASES if c["submit"]}
    preexisting = {}
    if sf:
        for name in submit_companies:
            preexisting[name] = snapshot_account_ids(sf, name)
    protected_ids = set()
    for ids in preexisting.values():
        protected_ids |= ids

    # Boot the http MCP server only if the dataset exercises the http transport.
    need_http = any(c["transport"] == "http" for c in CASES)
    http = HttpMcpServer()
    http_available = False
    if need_http:
        print(f"[eval] Booting http MCP server for the http case(s) at {MCP_HTTP_URL} ...")
        http_available = http.start()
        print(f"[eval] http MCP server available: {http_available}")

    created_plans = []
    created_accounts = []
    case_results = []

    try:
        for case in CASES:
            print(f"[eval] Running '{case['id']}' ({case['company']}) ...")
            steps, terminal, analyze_elapsed = run_analyze(case["company"], case["use_memory"])
            analyze_success, accuracy, acc_label, detail = score_analyze(case, terminal)
            output = extract_output(terminal)

            submit_result = None
            if case["submit"] and terminal and terminal[0] == "result":
                submit_result = run_submit(case, terminal[1]["plan"], http_available)
                if submit_result.get("status") == "PASS":
                    d = submit_result.get("details") or {}
                    if d.get("account_plan_id"):
                        created_plans.append((case["id"], d["account_plan_id"]))
                    if d.get("account_id"):
                        created_accounts.append((case["id"], d["account_id"]))

            # Headline success folds in the submit expectation.
            success = analyze_success
            if case["submit"]:
                if submit_result is None:
                    success = False  # was supposed to submit but had no plan
                elif submit_result.get("status") == "SKIPPED":
                    pass  # don't penalize analyze success for an unavailable server
                else:
                    success = success and (submit_result.get("status") == "PASS")

            total_elapsed = analyze_elapsed + (
                (submit_result or {}).get("elapsed") or 0.0
            )

            case_results.append(
                {
                    "id": case["id"],
                    "company": case["company"],
                    "use_memory": case["use_memory"],
                    "transport": case["transport"],
                    "submit": case["submit"],
                    "expect": case["expect"],
                    "note": case["note"],
                    "success": success,
                    "accuracy": round(accuracy, 3),
                    "accuracy_label": acc_label,
                    "detail": detail,
                    "analyze_elapsed": analyze_elapsed,
                    "submit_elapsed": (submit_result or {}).get("elapsed"),
                    "total_elapsed": round(total_elapsed, 2),
                    "steps": steps,
                    "submit_status": (submit_result or {}).get("status") if case["submit"] else None,
                    "submit_message": (submit_result or {}).get("message") if case["submit"] else None,
                    "submit_details": (submit_result or {}).get("details") if case["submit"] else None,
                    "output": output,
                }
            )
            print(
                f"[eval]   success={success} accuracy={accuracy:.0%} "
                f"analyze={analyze_elapsed}s submit={(submit_result or {}).get('status')}"
            )
    finally:
        cleanup_log = cleanup(sf, created_plans, created_accounts, protected_ids)
        if need_http:
            http.stop()

    # ---- Aggregate metrics ----
    n = len(case_results)
    successes = sum(1 for r in case_results if r["success"])
    accuracies = [r["accuracy"] for r in case_results]
    analyze_latencies = [r["analyze_elapsed"] for r in case_results]
    total_duration = round(time.perf_counter() - started, 2)

    metrics = {
        "cases": n,
        "successes": successes,
        "success_rate": round(successes / n, 3) if n else 0.0,
        "avg_accuracy": round(statistics.mean(accuracies), 3) if accuracies else 0.0,
        "avg_analyze_latency": round(statistics.mean(analyze_latencies), 2) if analyze_latencies else 0.0,
        "median_analyze_latency": round(statistics.median(analyze_latencies), 2) if analyze_latencies else 0.0,
        "max_analyze_latency": round(max(analyze_latencies), 2) if analyze_latencies else 0.0,
        "total_duration": total_duration,
    }

    payload = {
        "generated_at": started_iso,
        "http_available": http_available if need_http else None,
        "metrics": metrics,
        "cases": case_results,
        "cleanup": cleanup_log,
        "preexisting_accounts": {k: sorted(v) for k, v in preexisting.items()},
    }

    # ---- Write results + dashboard ----
    results_path = os.path.join(RESULTS_DIR, "results.json")
    with open(results_path, "w") as f:
        json.dump(payload, f, indent=2)

    dashboard_path = os.path.join(RESULTS_DIR, "dashboard.html")
    with open(dashboard_path, "w") as f:
        f.write(build_dashboard(payload))

    # ---- Console summary ----
    print("\n" + "=" * 64)
    print("EVAL SUMMARY")
    print("=" * 64)
    print(f"  Cases            : {n}")
    print(f"  Success rate     : {successes}/{n} ({metrics['success_rate']:.0%})")
    print(f"  Avg accuracy     : {metrics['avg_accuracy']:.0%}")
    print(f"  Avg analyze time : {metrics['avg_analyze_latency']}s "
          f"(median {metrics['median_analyze_latency']}s, max {metrics['max_analyze_latency']}s)")
    print(f"  Total duration   : {total_duration}s")
    print("  Cleanup:")
    print(f"    - AccountPlans deleted : {len(cleanup_log['deleted_plans'])}")
    print(f"    - Accounts deleted     : {len(cleanup_log['deleted_accounts'])}")
    print(f"    - Accounts protected   : {len(cleanup_log['protected_accounts'])}")
    if cleanup_log["errors"]:
        print(f"    - Cleanup errors       : {cleanup_log['errors']}")
    print(f"\n  Dashboard : {dashboard_path}")
    print(f"  Raw JSON  : {results_path}")
    print("=" * 64)

    return payload


# --------------------------------------------------------------------------- #
# Dashboard (self-contained HTML; data embedded so it opens over file://)       #
# --------------------------------------------------------------------------- #
def build_dashboard(payload):
    template = _DASHBOARD_TEMPLATE
    return template.replace("/*__DATA__*/null", json.dumps(payload))


_DASHBOARD_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Competitive Analysis Agent — Eval Dashboard</title>
<style>
  :root {
    --bg:#eef1f6; --bg-accent:#e7ecff; --card:#fff; --border:#e5e8ef;
    --text:#1f2733; --muted:#6b7684; --accent:#4f46e5; --accent-2:#7c3aed;
    --ok-bg:#ecfdf3; --ok-bd:#abefc6; --ok-tx:#067647;
    --bad-bg:#fef3f2; --bad-bd:#fecdca; --bad-tx:#b42318;
    --warn-bg:#fffaeb; --warn-bd:#fedf89; --warn-tx:#b54708;
    --radius:14px; --shadow:0 6px 24px rgba(31,39,51,.06);
  }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; color:var(--text);
    background:radial-gradient(1200px 600px at 50% -10%, var(--bg-accent), var(--bg)) fixed;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
    line-height:1.5; }
  .page { max-width:1040px; margin:0 auto; padding:48px 22px 96px; }
  header { text-align:center; margin-bottom:26px; }
  h1 { font-size:28px; margin:0 0 6px; letter-spacing:-.02em;
    background:linear-gradient(90deg,var(--accent),var(--accent-2));
    -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; }
  .sub { color:var(--muted); margin:0; font-size:14px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:14px; margin:24px 0; }
  .metric { background:var(--card); border:1px solid var(--border); border-radius:var(--radius);
    padding:18px; box-shadow:var(--shadow); }
  .metric .k { font-size:12px; color:var(--muted); font-weight:600; text-transform:uppercase; letter-spacing:.04em; }
  .metric .v { font-size:26px; font-weight:700; margin-top:6px; }
  .metric .v.good { color:var(--ok-tx); } .metric .v.bad { color:var(--bad-tx); }
  .card { background:var(--card); border:1px solid var(--border); border-radius:var(--radius);
    padding:22px; box-shadow:var(--shadow); margin-bottom:20px; }
  .card h2 { margin:0 0 14px; font-size:18px; }
  .chip { display:inline-block; font-size:11px; font-weight:700; padding:3px 9px; border-radius:999px;
    text-transform:uppercase; letter-spacing:.03em; }
  .chip.pass { background:var(--ok-bg); color:var(--ok-tx); border:1px solid var(--ok-bd); }
  .chip.fail { background:var(--bad-bg); color:var(--bad-tx); border:1px solid var(--bad-bd); }
  .chip.skip { background:var(--warn-bg); color:var(--warn-tx); border:1px solid var(--warn-bd); }
  .badge { display:inline-block; font-size:11px; font-weight:600; padding:2px 8px; border-radius:6px;
    background:#eef2ff; color:var(--accent); margin-right:6px; }
  .badge.gray { background:#f1f3f7; color:var(--muted); }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:10px 10px; border-bottom:1px solid var(--border); vertical-align:top; }
  th { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
  .bar-wrap { background:#eef1f6; border-radius:6px; height:22px; position:relative; overflow:hidden; min-width:120px; }
  .bar { height:100%; float:left; }
  .bar.analyze { background:linear-gradient(90deg,var(--accent),var(--accent-2)); }
  .bar.submit { background:#22a06b; }
  .bar-label { font-size:11px; color:var(--muted); margin-top:3px; }
  .case { border:1px solid var(--border); border-radius:12px; padding:16px; margin-bottom:14px; }
  .case-head { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .case-title { font-weight:700; font-size:15px; }
  .case-note { color:var(--muted); font-size:13px; margin:8px 0 10px; }
  .kv { font-size:12.5px; color:var(--muted); margin:2px 0; }
  details { margin-top:10px; }
  summary { cursor:pointer; font-size:13px; font-weight:600; color:var(--accent); }
  .plan-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(230px,1fr)); gap:12px; margin-top:12px; }
  .plan-field { background:#fafbfd; border:1px solid var(--border); border-radius:10px; padding:12px; }
  .plan-field h4 { margin:0 0 6px; font-size:12px; text-transform:uppercase; letter-spacing:.03em; color:var(--muted); }
  .plan-field ul { margin:0; padding-left:18px; font-size:13px; }
  .plan-field li { margin-bottom:3px; }
  .err { background:var(--bad-bg); border:1px solid var(--bad-bd); color:var(--bad-tx);
    border-radius:10px; padding:12px; font-size:13px; margin-top:10px; }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }
  .cleanup-item { font-size:13px; margin:3px 0; }
  .foot { color:var(--muted); font-size:12px; text-align:center; margin-top:20px; }
</style>
</head>
<body>
<div class="page">
  <header>
    <h1>Competitive Analysis Agent — Eval Dashboard</h1>
    <p class="sub" id="subtitle"></p>
  </header>
  <div class="cards" id="metrics"></div>
  <div class="card">
    <h2>Latency by case</h2>
    <table id="latency"></table>
    <p class="bar-label" style="margin-top:10px;">
      <span class="badge">analyze</span> Agents 1-4 &nbsp;
      <span class="badge" style="background:#e6f6ee;color:#22a06b;">submit</span> Agent 5 (Salesforce write via MCP)
    </p>
  </div>
  <div class="card">
    <h2>Cases</h2>
    <div id="cases"></div>
  </div>
  <div class="card">
    <h2>Salesforce cleanup</h2>
    <div id="cleanup"></div>
  </div>
  <p class="foot" id="foot"></p>
</div>
<script>
const DATA = /*__DATA__*/null;

const esc = (s) => String(s == null ? "" : s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const humanize = (k) => k.replace(/_/g,' ').replace(/\b\w/g, c => c.toUpperCase());
const pct = (x) => (x*100).toFixed(0) + '%';

function metricCards(m) {
  const srGood = m.success_rate >= 0.999;
  const cards = [
    {k:'Cases', v:m.cases},
    {k:'Success rate', v:`${m.successes}/${m.cases} (${pct(m.success_rate)})`, cls: srGood?'good':'bad'},
    {k:'Avg accuracy', v:pct(m.avg_accuracy), cls: m.avg_accuracy>=0.999?'good':''},
    {k:'Avg analyze latency', v:`${m.avg_analyze_latency}s`},
    {k:'Max analyze latency', v:`${m.max_analyze_latency}s`},
    {k:'Total duration', v:`${m.total_duration}s`},
  ];
  document.getElementById('metrics').innerHTML = cards.map(c =>
    `<div class="metric"><div class="k">${esc(c.k)}</div><div class="v ${c.cls||''}">${esc(c.v)}</div></div>`
  ).join('');
}

function latencyTable(cases) {
  const max = Math.max(1, ...cases.map(c => c.total_elapsed || c.analyze_elapsed || 0));
  const rows = cases.map(c => {
    const a = c.analyze_elapsed || 0;
    const s = c.submit_elapsed || 0;
    const aw = (a/max*100).toFixed(1);
    const sw = (s/max*100).toFixed(1);
    return `<tr>
      <td><span class="mono">${esc(c.id)}</span></td>
      <td style="width:55%">
        <div class="bar-wrap">
          <div class="bar analyze" style="width:${aw}%" title="analyze ${a}s"></div>
          <div class="bar submit" style="width:${sw}%" title="submit ${s}s"></div>
        </div>
      </td>
      <td class="mono">${a}s${s?` + ${s}s`:''}</td>
    </tr>`;
  }).join('');
  document.getElementById('latency').innerHTML =
    `<tr><th>Case</th><th>Wall-clock</th><th>Time</th></tr>` + rows;
}

function submitChip(c) {
  if (!c.submit) return '<span class="badge gray">no submit</span>';
  if (c.submit_status === 'PASS') return '<span class="chip pass">SF write ✓</span>';
  if (c.submit_status === 'SKIPPED') return '<span class="chip skip">SF write skipped</span>';
  return '<span class="chip fail">SF write ✗</span>';
}

function planBlock(output) {
  if (output.kind === 'error') {
    return `<div class="err"><b>Failed at stage:</b> ${esc(output.stage)}<br>${esc(output.message)}</div>`;
  }
  if (output.kind !== 'plan') return '<div class="kv">No output.</div>';
  const comp = (output.competitors||[]).map(x=>`<span class="badge">${esc(x)}</span>`).join('') || '<span class="kv">none</span>';
  const fields = Object.entries(output.plan||{}).map(([k,v]) => {
    const items = (Array.isArray(v)?v:[v]).map(i=>`<li>${esc(i)}</li>`).join('');
    return `<div class="plan-field"><h4>${esc(humanize(k))}</h4><ul>${items}</ul></div>`;
  }).join('');
  return `<div class="kv"><b>Competitors:</b> ${comp}</div><div class="plan-grid">${fields}</div>`;
}

function caseCards(cases) {
  document.getElementById('cases').innerHTML = cases.map(c => {
    const statusChip = c.success ? '<span class="chip pass">pass</span>' : '<span class="chip fail">fail</span>';
    const badges = [
      `<span class="badge ${c.use_memory?'':'gray'}">memory ${c.use_memory?'on':'off'}</span>`,
      `<span class="badge">${esc(c.transport)}</span>`,
      `<span class="badge gray">expect: ${esc(c.expect)}</span>`,
    ].join('');
    const ids = c.submit_details && (c.submit_details.account_plan_id || c.submit_details.account_id)
      ? `<div class="kv mono">account=${esc(c.submit_details.account_id||'-')} · plan=${esc(c.submit_details.account_plan_id||'-')}</div>` : '';
    return `<div class="case">
      <div class="case-head">
        ${statusChip}
        <span class="case-title">${esc(c.company)}</span>
        <span class="mono" style="color:var(--muted)">${esc(c.id)}</span>
        <span style="margin-left:auto">${submitChip(c)}</span>
      </div>
      <div class="case-note">${esc(c.note)}</div>
      <div>${badges}</div>
      <div class="kv" style="margin-top:8px"><b>Accuracy:</b> ${pct(c.accuracy)} — ${esc(c.accuracy_label)}</div>
      <div class="kv"><b>Latency:</b> analyze ${c.analyze_elapsed}s${c.submit_elapsed?` · submit ${c.submit_elapsed}s`:''}</div>
      ${c.submit_message?`<div class="kv"><b>SF:</b> ${esc(c.submit_message)}</div>`:''}
      ${ids}
      <details><summary>Output</summary>${planBlock(c.output)}</details>
    </div>`;
  }).join('');
}

function cleanupBlock(cl) {
  const line = (label, arr) => `<div class="cleanup-item"><b>${label}:</b> ${arr.length}${arr.length?` — <span class="mono">${arr.map(x=>esc(x.id)).join(', ')}</span>`:''}</div>`;
  let html = line('AccountPlans deleted', cl.deleted_plans)
           + line('Accounts deleted (created by run)', cl.deleted_accounts)
           + line('Accounts protected (pre-existing)', cl.protected_accounts);
  if (cl.errors && cl.errors.length) {
    html += `<div class="err">Cleanup errors:<br>${cl.errors.map(esc).join('<br>')}</div>`;
  } else {
    html += `<div class="cleanup-item" style="color:var(--ok-tx)">No cleanup errors — org left clean.</div>`;
  }
  document.getElementById('cleanup').innerHTML = html;
}

function render() {
  const d = DATA;
  document.getElementById('subtitle').textContent =
    `Generated ${d.generated_at}` + (d.http_available===null?'':` · http MCP server ${d.http_available?'up':'unavailable'}`);
  metricCards(d.metrics);
  latencyTable(d.cases);
  caseCards(d.cases);
  cleanupBlock(d.cleanup);
  document.getElementById('foot').textContent = 'Competitive Analysis Agent evaluation — self-contained report.';
}
render();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
