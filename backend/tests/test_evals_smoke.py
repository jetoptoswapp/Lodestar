"""eval 框架 smoke：用假 adapter 跑 run_case，確保框架不爛（不需真實 CLI）。"""
from __future__ import annotations

from plugin_api import ModelAdapter


def _fake(reg, text):
    reg.model_adapters["claude-cli"] = ModelAdapter(
        model_choice="claude-cli", invoke=lambda p: text, is_available=lambda: True,
        description="", max_context_tokens=100000, prompt_budget_tokens=90000,
        response_budget_tokens=2000)


def test_run_case_pass(tmp_db):
    import plugin_loader as L
    from evals.run_evals import run_case
    reg = L.load_all()
    _fake(reg, "# PRD\n## 3. Functional Requirements\nFR-1 登入\n## 4. NFR\nNFR-1 並發 1000")
    case = {"id": "smoke", "stage": "prd", "operation": "generate_prd",
            "workflow": "default", "conversation": [["user", "登入系統"]],
            "expect": {"must_contain": ["FR-", "NFR-"]}}
    r = run_case(reg, case)
    assert r["passed"] is True and r["error_code"] == "" and r["missing"] == []


def test_run_case_fail_on_missing(tmp_db):
    import plugin_loader as L
    from evals.run_evals import run_case
    reg = L.load_all()
    _fake(reg, "incomplete output without requirements")
    case = {"id": "smoke2", "stage": "prd", "operation": "generate_prd",
            "expect": {"must_contain": ["FR-", "NFR-"]}}
    r = run_case(reg, case)
    assert r["passed"] is False and set(r["missing"]) == {"FR-", "NFR-"}


def test_loaded_case_file_is_valid(tmp_db):
    """確認 cases/ 內的 golden case 檔可載入且結構完整。"""
    from pathlib import Path
    from evals.run_evals import load_cases
    cases = load_cases(Path(__file__).resolve().parent.parent / "evals" / "cases")
    assert cases and all("id" in c and "stage" in c and "expect" in c for c in cases)
