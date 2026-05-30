"""Golden / regression eval harness。

對每個 case 跑 stage 的 generate（經 WorkflowEngine.dispatch），對 artifact 跑 must_contain
斷言 + （可選）judge 評分，輸出通過率報表。eval 跑真實 model 較慢、非 CI 必跑；核心 run_case
可注入假 adapter（見 tests/test_evals_smoke.py）保證框架不爛。

用法：
  backend/.venv/bin/python backend/evals/run_evals.py \
      [--judge claude-cli] [--cases backend/evals/cases] [--baseline prev.json] [--out result.json]

判定：error_code 為空 且 無 must_contain 缺漏 且 無 judge fail → passed。
回傳碼：有任何 case fail 或相對 baseline 退步 → 1，否則 0（供 CI gate）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def run_case(reg, case: dict, *, judge_model: str = "") -> dict:
    """跑單一 case：dispatch generate → 取 artifact → must_contain + judge。回 result dict。"""
    from persistence import dal
    from workflow_engine import WorkflowEngine

    tid = f"eval_{case['id']}"
    dal.create_project(tid, case["id"], workflow_id=case.get("workflow", "default"))
    for sid, art in (case.get("upstream") or {}).items():
        dal.upsert_artifact(tid, sid, art)
    for role, content in (case.get("conversation") or []):
        dal.append_message(tid, case["stage"], role, content)

    engine = WorkflowEngine(reg)
    if judge_model:
        engine._judge_model_choice = lambda: judge_model
    out = engine.dispatch(thread_id=tid, stage_id=case["stage"], op="generate",
                          model_choice=case.get("model_choice", "claude-cli"))

    artifact = out.get("artifact", "")
    expect = case.get("expect", {})
    missing = [s for s in expect.get("must_contain", []) if s not in artifact]
    judge_fail = any(v["severity"] == "fail" and "judge" in v["validator"]
                     for v in out.get("validations", []))
    passed = (not out.get("error_code")) and (not missing) and (not judge_fail)
    return {
        "id": case["id"], "passed": passed, "error_code": out.get("error_code", ""),
        "missing": missing, "judge_fail": judge_fail, "artifact_len": len(artifact),
    }


def load_cases(cases_dir: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(cases_dir.glob("*.json"))]


def run_all(cases: list[dict], *, judge_model: str = "") -> dict:
    import plugin_loader as L
    reg = L.load_all()
    results = [run_case(reg, c, judge_model=judge_model) for c in cases]
    n_pass = sum(1 for r in results if r["passed"])
    return {"total": len(results), "passed": n_pass,
            "pass_rate": round(n_pass / len(results), 3) if results else 0.0,
            "results": results}


def compare_baseline(current: dict, baseline: dict) -> list[str]:
    """回退步清單（baseline passed 但 current fail 的 case id）—— regression 偵測。"""
    base = {r["id"]: r["passed"] for r in baseline.get("results", [])}
    return [r["id"] for r in current["results"]
            if base.get(r["id"]) and not r["passed"]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge", default="", help="judge model_choice（空=不跑 judge）")
    ap.add_argument("--cases", default=str(Path(__file__).parent / "cases"))
    ap.add_argument("--baseline", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    cases = load_cases(Path(args.cases))
    if not cases:
        print("no cases found", file=sys.stderr)
        return 2
    report = run_all(cases, judge_model=args.judge)
    print(f"eval: {report['passed']}/{report['total']} passed (rate={report['pass_rate']})")
    for r in report["results"]:
        mark = "PASS" if r["passed"] else "FAIL"
        extra = "" if r["passed"] else (
            f"  missing={r['missing']} judge_fail={r['judge_fail']} err={r['error_code']}")
        print(f"  [{mark}] {r['id']}{extra}")

    regressed: list[str] = []
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        regressed = compare_baseline(report, baseline)
        if regressed:
            print(f"REGRESSED vs baseline: {regressed}", file=sys.stderr)
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if (report["passed"] < report["total"] or regressed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
