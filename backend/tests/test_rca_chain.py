"""rca_chain（RCA-2 多代理鏈）：catalog / 依賴鏈 / 缺上游擋關 / mock 端到端 / 因果圖 validator / 下游 reset。

全部不呼叫真實模型。
"""
from __future__ import annotations

import pytest

from plugin_api import ModelAdapter
from plugin_api.harness import HarnessContext
from workflow_engine import MissingDependencyError, WorkflowEngine

import app as appmod
from plugins.rca_domain.chain_stages import _causal_graph_validator


# mock 輸出：同時滿足因果圖（mermaid）與彙整（≥3 候選表 + 證據 + 下一步 + 免責）validator
_MOCK_OUT = """## Result
```mermaid
graph TD
  A[ETCH-03 param drift] --> B[yield drop]
```
| Rank | Candidate root cause | Confidence | Evidence | Suggested next check |
|------|----------------------|------------|----------|----------------------|
| 1 | ETCH-03 chamber drift | high | ETCH-03 05-21 起 L2231 落 76% | 拉 ETCH-03 PM/log |
| 2 | consumable wear | medium | 集中單機 | 查換件紀錄 |
| 3 | material lot | low | recipe 未變 | 比對進料批 |

> 候選假設 · 待工程師確認，非結論 — candidate hypotheses for engineer confirmation, not conclusions.
"""


def _install_mock(reg, response: str = _MOCK_OUT) -> None:
    reg.model_adapters["claude-cli"] = ModelAdapter(
        model_choice="claude-cli", invoke=lambda p: response, is_available=lambda: True,
        description="mock", max_context_tokens=1000, prompt_budget_tokens=900, response_budget_tokens=100,
    )


def test_rca_chain_catalog(tmp_db):
    import plugin_loader as L
    reg = L.load_all()
    for sid in ("rca_baseline", "rca_causal", "rca_knowledge", "rca_synthesis"):
        assert sid in reg.stages, sid
    assert reg.stages["rca_causal"].depends_on == ("rca_baseline",)
    assert reg.stages["rca_knowledge"].depends_on == ("rca_baseline",)
    assert reg.stages["rca_synthesis"].depends_on == ("rca_causal", "rca_knowledge")
    assert "rca_chain" in reg.workflows
    assert reg.workflows["rca_chain"].stages == (
        "rca_intake", "rca_baseline", "rca_causal", "rca_knowledge", "rca_synthesis")
    for aid in ("rca_baseline_analyst", "rca_causal_reasoner", "rca_knowledge_agent", "rca_synthesizer"):
        assert aid in reg.agents, aid


def test_rca_chain_in_api(tmp_db):
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        by_id = {s["id"]: s for s in c.get("/api/stages").json()["stages"]}
        assert {"rca_baseline", "rca_causal", "rca_knowledge", "rca_synthesis"}.issubset(by_id)
        # synthesis 下游無；causal/knowledge 上游 baseline
        assert by_id["rca_synthesis"]["depends_on"] == ["rca_causal", "rca_knowledge"]
        wf = {w["id"]: w for w in c.get("/api/workflows").json()["workflows"]}
        assert "rca_chain" in wf


def test_rca_synthesis_blocks_without_upstream(tmp_db):
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    _install_mock(reg)
    dal.create_project("t1", "chain", workflow_id="rca_chain")
    dal.upsert_artifact("t1", "rca_intake", "anomaly")
    engine = WorkflowEngine(reg)
    # synthesis 需 causal + knowledge，皆缺 → MissingDependencyError
    with pytest.raises(MissingDependencyError):
        engine.dispatch(thread_id="t1", stage_id="rca_synthesis", op="generate")


def test_rca_chain_flow_mock(tmp_db):
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    _install_mock(reg)
    dal.create_project("t1", "chain", workflow_id="rca_chain")
    dal.upsert_artifact("t1", "rca_intake", "Line-3 RX-7 良率自 05-21 步階下降")
    engine = WorkflowEngine(reg)

    for sid in ("rca_baseline", "rca_causal", "rca_knowledge", "rca_synthesis"):
        out = engine.dispatch(thread_id="t1", stage_id=sid, op="generate")
        assert out["error_code"] == "", f"{sid}: {out['error_code']}"
        assert dal.get_artifact("t1", sid), f"{sid} artifact empty"
        assert dal.get_stage_status("t1", sid) == "draft"

    # 因果圖含 mermaid；彙整含候選表
    assert "mermaid" in dal.get_artifact("t1", "rca_causal")
    assert "Candidate root cause" in dal.get_artifact("t1", "rca_synthesis")


def test_causal_graph_validator():
    ctx = HarnessContext(thread_id="t", stage="rca_causal", operation="generate_rca_causal",
                         model_choice="mock", prompt="")
    assert _causal_graph_validator("```mermaid\ngraph TD\nA-->B\n```", ctx) == []
    out = _causal_graph_validator("沒有圖只有文字", ctx)
    assert len(out) == 1 and out[0].validator == "rca.causal_graph"


def test_rca_chain_downstream_reset(tmp_db):
    import plugin_loader as L
    from persistence import dal
    reg = L.load_all()
    dal.create_project("t1", "chain", workflow_id="rca_chain")
    # 全鏈先 approved
    for sid in ("rca_intake", "rca_baseline", "rca_causal", "rca_knowledge", "rca_synthesis"):
        dal.upsert_artifact("t1", sid, f"{sid} content")
        dal.set_stage_status("t1", sid, "approved")

    _install_mock(reg)
    engine = WorkflowEngine(reg)
    out = engine.dispatch(thread_id="t1", stage_id="rca_baseline", op="refine", instruction="加一點")
    # baseline 改 → causal / knowledge / synthesis 全 reset
    assert set(out["downstream_reset"]) == {"rca_causal", "rca_knowledge", "rca_synthesis"}
    assert dal.get_stage_status("t1", "rca_causal") == "needs_revision"
    assert dal.get_stage_status("t1", "rca_synthesis") == "needs_revision"
    assert dal.get_stage_status("t1", "rca_intake") == "approved"  # 上游不動
