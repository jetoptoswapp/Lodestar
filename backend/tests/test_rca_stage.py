"""rca_domain（RCA-1 單代理）：catalog 註冊 / API 露出 / 依賴擋關 / mock 端到端 / validators。

全部不呼叫真實模型（用 mock adapter / 直接測 validator 純函式）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from plugin_api import ModelAdapter
from plugin_api.harness import HarnessContext
from workflow_engine import MissingDependencyError, WorkflowEngine

import app as appmod
from plugins.rca_domain.analysis_stage import (
    _candidate_causes_validator,
    _copilot_disclaimer_validator,
    _count_candidate_rows,
)


# 一份「合格」的候選根因分析（≥3 候選表 + 證據 + 下一步 + 免責聲明）
_GOOD_ANALYSIS = """## Baseline vs. Anomaly
ETCH-03 良率自 2026-05-21 後從約 96% 步階下降到約 76%，其他機台維持 ~96%。

## Candidate Root Causes
| Rank | Candidate root cause | Confidence | Evidence | Suggested next check |
|------|----------------------|------------|----------|----------------------|
| 1 | ETCH-03 製程/腔體漂移 | high | ETCH-03 lots L2231+ ~76% vs 其他機台 ~96% | 調 ETCH-03 自 05-21 起的腔體壓力/RF log |
| 2 | ETCH-03 耗材/零件磨耗 | medium | 步階僅集中單一機台 | 查 ETCH-03 PM 與換件紀錄 |
| 3 | 進料批交互作用 | low | recipe RX-7 跨機台不變 | 比對 L2231+ 進料批號 |

## Suggested Check Order
1. ETCH-03 腔體 log（便宜、高影響）
2. ETCH-03 PM 紀錄
3. 進料批號

> 這些是供工程師確認的候選假設，非最終結論；真正根因須於現場核對設備、製程與維修紀錄。
"""


def _mock_ctx() -> HarnessContext:
    return HarnessContext(
        thread_id="t", stage="rca_analysis",
        operation="generate_rca_analysis", model_choice="mock", prompt="",
    )


def _install_mock_adapter(registry, response: str) -> None:
    registry.model_adapters["claude-cli"] = ModelAdapter(
        model_choice="claude-cli",
        invoke=lambda prompt: response,
        is_available=lambda: True,
        description="mock", max_context_tokens=1000,
        prompt_budget_tokens=900, response_budget_tokens=100,
    )


# ============ catalog 註冊 ============
def test_rca_catalog_registered(tmp_db):
    import plugin_loader as L
    reg = L.load_all()

    assert "rca_intake" in reg.stages
    assert "rca_analysis" in reg.stages
    assert reg.stages["rca_analysis"].depends_on == ("rca_intake",)
    assert reg.stages["rca_intake"].telemetry_stage == "rca_intake"
    assert reg.stages["rca_intake"].supports_chat is True

    assert "rca_single" in reg.workflows
    assert reg.workflows["rca_single"].stages == ("rca_intake", "rca_analysis")

    assert "rca_assistant" in reg.agents
    assert reg.agents["rca_assistant"].role == "rca_analysis"


def test_rca_stages_in_api(tmp_db):
    with TestClient(appmod.app) as client:
        by_id = {s["id"]: s for s in client.get("/api/stages").json()["stages"]}
        assert {"rca_intake", "rca_analysis"}.issubset(by_id)
        analysis = by_id["rca_analysis"]
        assert analysis["depends_on"] == ["rca_intake"]
        assert {"generate", "refine", "chat"}.issubset(set(analysis["operations"]))
        assert analysis["plugin_id"] == "rca_domain"

        wf_ids = {w["id"] for w in client.get("/api/workflows").json()["workflows"]}
        assert "rca_single" in wf_ids


# ============ 依賴擋關（不呼叫模型）============
def test_rca_analysis_blocks_without_intake(tmp_db):
    import plugin_loader as L
    from persistence import dal

    reg = L.load_all()
    _install_mock_adapter(reg, _GOOD_ANALYSIS)
    dal.create_project("t1", "rca test", workflow_id="rca_single")

    engine = WorkflowEngine(reg)
    with pytest.raises(MissingDependencyError) as exc:
        engine.dispatch(thread_id="t1", stage_id="rca_analysis", op="generate")
    assert exc.value.missing_upstream == "rca_intake"


# ============ mock 端到端：單代理 generate ============
def test_rca_single_agent_flow(tmp_db):
    import plugin_loader as L
    from persistence import dal

    reg = L.load_all()
    _install_mock_adapter(reg, _GOOD_ANALYSIS)
    dal.create_project("t1", "rca test", workflow_id="rca_single")
    dal.upsert_artifact("t1", "rca_intake", "Line-3 RX-7 良率自 05-21 後步階下降。")

    engine = WorkflowEngine(reg)
    out = engine.dispatch(thread_id="t1", stage_id="rca_analysis", op="generate")

    assert out["error_code"] == ""
    assert "Candidate Root Causes" in out["artifact"]
    assert out["downstream_reset"] == []          # rca_analysis 無下游
    assert dal.get_artifact("t1", "rca_analysis").startswith("## Baseline")
    assert dal.get_stage_status("t1", "rca_analysis") == "draft"
    revs = dal.list_revisions("t1", "rca_analysis")
    assert len(revs) == 1 and revs[0]["source"] == "generate_rca_analysis"


# ============ validators（純函式）============
def test_candidate_causes_validator_passes_on_good():
    assert _candidate_causes_validator(_GOOD_ANALYSIS, _mock_ctx()) == []
    assert _count_candidate_rows(_GOOD_ANALYSIS) >= 3


def test_candidate_causes_validator_warns_on_bad():
    bad = "我覺得可能是機台問題，再看看。"
    codes = {o.validator for o in _candidate_causes_validator(bad, _mock_ctx())}
    assert "rca.min_candidates" in codes
    assert "rca.has_next_check" in codes


def test_copilot_disclaimer_validator():
    assert _copilot_disclaimer_validator(_GOOD_ANALYSIS, _mock_ctx()) == []
    bad = "## Candidate Root Causes\n根因就是 ETCH-03 腔體故障。"
    out = _copilot_disclaimer_validator(bad, _mock_ctx())
    assert len(out) == 1 and out[0].validator == "rca.copilot_disclaimer"
