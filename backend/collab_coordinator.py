"""collab_coordinator —— 多代理協作執行（spec §6.4：discussion / dispatch）。

host 層模組（非 plugin），可 import harness_runner + dal（如 workflow_engine 一樣）。
WorkflowEngine.dispatch 在 generate 且 collab_mode∈{discussion,dispatch} 且 binding>1 時呼叫
run_collab，回傳合成後的 artifact；其餘 I/O（寫 artifact / reset 下游 / event）仍由 engine 統一處理。

設計（沿用已驗證的 sync HarnessRunner，不硬套 async impl runtime）：
- discussion：peer 依序發言（各帶自身 system_prompt + 共享脈絡 + 先前發言），寫進 stage_messages，
  最後 lead 以 generate_{stage} operation 合成 artifact（會經 stage 的 validator）。
- dispatch  ：lead 先把任務拆成子任務（JSON）→ subagent 經 ThreadPoolExecutor 平行各跑一段，
  lead 再合併成 artifact。

兩個缺口的處理（spec 註記）：
- GAP A：agent_id 解析 —— resolve_agent 先查 registry.agents，再 fallback dal.get_agent。
- GAP B：per-agent system_prompt + model_choice —— coordinator 自己組 prompt、每個 agent 建一個
  HarnessRunner（用該 agent 的 model_choice）。
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from plugin_api import AgentSpec
from plugin_api.workflow import AgentBinding

from harness_runner import HarnessRunner
from persistence import dal

log = logging.getLogger("collab_coordinator")

_MAX_SUBAGENTS = 4   # PoC 上限，避免一次開太多 subprocess


class CollabError(RuntimeError):
    """coordinator 內部錯誤（無 lead / 全員無法解析）；被 engine 當 harness error 接住。"""


# ============================================================
#  Agent 解析（GAP A）
# ============================================================
def resolve_agent(registry, agent_id: str) -> Optional[AgentSpec]:
    """registry.agents（plugin seed）→ fallback dal.get_agent（user DB agent）。"""
    spec = registry.agents.get(agent_id)
    if spec is not None:
        return spec
    row = dal.get_agent(agent_id)
    if row is None:
        return None
    return AgentSpec(
        agent_id=row["agent_id"], name=row["name"], role=row["role"],
        system_prompt=row.get("system_prompt", ""), model_choice=row.get("model_choice", "claude-cli"),
        tools=tuple(row.get("tools") or ()), max_iterations=row.get("max_iterations", 1),
        enabled=bool(row.get("enabled", True)),
    )


def _split_roles(bindings: tuple[AgentBinding, ...]):
    leads = [b for b in bindings if b.role == "lead"]
    peers = [b for b in bindings if b.role == "peer"]
    subs = [b for b in bindings if b.role == "subagent"]
    return leads, peers, subs


# ============================================================
#  共享脈絡（upstream artifacts + 現有草稿 + 附件提示）
# ============================================================
def _context_block(stage, ctx) -> str:
    parts = [f"# RCA stage：{stage.label}（{stage.id}）"]
    for key, val in (ctx.upstream_artifacts or {}).items():
        parts.append(f"\n## 上游 · {key}\n{val}")
    if ctx.current_artifact:
        parts.append(f"\n## 目前草稿\n{ctx.current_artifact}")
    atts = ctx.metadata.get("attachments", []) if ctx.metadata else []
    paths = [a.get("abs_path") for a in atts if a.get("abs_path")]
    if paths:
        parts.append("\n## 附件（用 Read tool 讀取作為證據）\n" + "\n".join(f"- {p}" for p in paths))
    return "\n".join(parts)


def _runner(registry, thread_id: str, stage_id: str, agent: AgentSpec) -> HarnessRunner:
    return HarnessRunner(registry, thread_id, stage_id, agent.model_choice or "claude-cli")


# ============================================================
#  discussion：peer 輪流 → lead 合成
# ============================================================
def run_discussion(registry, *, thread_id, stage, ctx, model_choice,
                   leads, peers) -> str:
    shared = _context_block(stage, ctx)
    lead_spec = resolve_agent(registry, leads[0].agent_id) if leads else None
    peer_specs = [s for s in (resolve_agent(registry, b.agent_id) for b in peers) if s is not None]
    if lead_spec is None and peer_specs:
        lead_spec = peer_specs[0]   # 無 lead → 退而求其次拿第一個 peer 當 lead
    if lead_spec is None:
        raise CollabError(f"stage '{stage.id}' discussion 無可解析的 agent")

    transcript: list[str] = []
    for spec in peer_specs:
        prompt = (
            f"{spec.system_prompt}\n\n{shared}\n\n"
            f"## 目前討論\n{chr(10).join(transcript) or '(尚無)'}\n\n"
            f"你是討論中的 specialist（{spec.name}）。給出你的角度：候選原因、佐證、以及一項下一步檢查。"
            f"精簡，最後由 lead 彙整。"
        )
        res = _runner(registry, thread_id, stage.id, spec).harnessed_step(
            telemetry_stage=stage.telemetry_stage, operation=f"discuss_{stage.id}",
            prompt=prompt, metadata={"thread_id": thread_id, "agent_id": spec.agent_id}, max_iterations=1,
        )
        turn = res.raw_output.strip()
        if turn:
            transcript.append(f"### {spec.name}\n{turn}")
            dal.append_message(thread_id, stage.id, "assistant", f"**{spec.name}（peer）**\n\n{turn}")

    lead_prompt = (
        f"{lead_spec.system_prompt}\n\n{shared}\n\n"
        f"## 多方討論\n{chr(10).join(transcript) or '(無 peer 發言)'}\n\n"
        f"你是 lead（{lead_spec.name}）。彙整以上討論成本 stage 的最終結果："
        f"排序的候選根因表（至少 3 個，含信心、證據、下一步檢查），"
        f"並於結尾聲明這些是候選假設、待工程師確認、非結論。"
    )
    res = _runner(registry, thread_id, stage.id, lead_spec).harnessed_step(
        telemetry_stage=stage.telemetry_stage, operation=f"generate_{stage.id}",
        prompt=lead_prompt, metadata={"thread_id": thread_id, "agent_id": lead_spec.agent_id}, max_iterations=1,
    )
    return res.raw_output.strip()


# ============================================================
#  dispatch：lead 拆 → subagent 平行 → lead 合併
# ============================================================
_JSON_LIST_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_subtasks(text: str, n: int) -> list[str]:
    m = _JSON_LIST_RE.search(text or "")
    if m:
        try:
            arr = json.loads(m.group(0))
            tasks = [str(x).strip() for x in arr if str(x).strip()]
            if tasks:
                return tasks[:n]
        except (json.JSONDecodeError, ValueError):
            pass
    # fallback：通用子任務
    return [f"從第 {i+1} 個角度分析此異常的候選根因與佐證" for i in range(n)]


def run_dispatch(registry, *, thread_id, stage, ctx, model_choice,
                 leads, subs) -> str:
    shared = _context_block(stage, ctx)
    lead_spec = resolve_agent(registry, leads[0].agent_id) if leads else None
    sub_specs = [s for s in (resolve_agent(registry, b.agent_id) for b in subs) if s is not None][:_MAX_SUBAGENTS]
    if lead_spec is None:
        if not sub_specs:
            raise CollabError(f"stage '{stage.id}' dispatch 無可解析的 agent")
        lead_spec = sub_specs[0]

    # 1) lead 拆任務
    split_prompt = (
        f"{lead_spec.system_prompt}\n\n{shared}\n\n"
        f"把此 RCA 拆成 {len(sub_specs)} 個聚焦子任務（每個 specialist 一個）。"
        f"只輸出一個 JSON 字串陣列（子任務描述），不要其他文字。"
    )
    split_out = _runner(registry, thread_id, stage.id, lead_spec).harnessed_step(
        telemetry_stage=stage.telemetry_stage, operation=f"dispatch_split_{stage.id}",
        prompt=split_prompt, metadata={"thread_id": thread_id}, max_iterations=1,
    ).raw_output
    subtasks = _parse_subtasks(split_out, len(sub_specs))

    # 2) subagent 平行執行
    def _work(idx_spec_task):
        idx, spec, task = idx_spec_task
        prompt = (
            f"{spec.system_prompt}\n\n{shared}\n\n"
            f"你的聚焦子任務：{task}\n"
            f"產出此角度的候選原因、佐證（引用資料）、與一項下一步檢查。"
        )
        out = _runner(registry, thread_id, stage.id, spec).harnessed_step(
            telemetry_stage=stage.telemetry_stage, operation=f"dispatch_worker_{stage.id}",
            prompt=prompt, metadata={"thread_id": thread_id, "agent_id": spec.agent_id}, max_iterations=1,
        ).raw_output.strip()
        return spec, out

    jobs = [(i, sub_specs[i], subtasks[i] if i < len(subtasks) else subtasks[-1])
            for i in range(len(sub_specs))]
    findings: list[tuple[AgentSpec, str]] = []
    if jobs:
        with ThreadPoolExecutor(max_workers=min(len(jobs), _MAX_SUBAGENTS)) as ex:
            findings = list(ex.map(_work, jobs))
    for spec, out in findings:
        if out:
            dal.append_message(thread_id, stage.id, "assistant", f"**{spec.name}（subagent）**\n\n{out}")

    # 3) lead 合併
    joined = "\n\n".join(f"### {spec.name}\n{out}" for spec, out in findings if out) or "(無 subagent 產出)"
    merge_prompt = (
        f"{lead_spec.system_prompt}\n\n{shared}\n\n"
        f"## Subagent findings\n{joined}\n\n"
        f"合併成最終結果：排序候選根因表（至少 3 個，含信心、證據、下一步檢查），"
        f"並於結尾聲明這些是候選假設、待工程師確認、非結論。"
    )
    res = _runner(registry, thread_id, stage.id, lead_spec).harnessed_step(
        telemetry_stage=stage.telemetry_stage, operation=f"generate_{stage.id}",
        prompt=merge_prompt, metadata={"thread_id": thread_id, "agent_id": lead_spec.agent_id}, max_iterations=1,
    )
    return res.raw_output.strip()


# ============================================================
#  dispatcher
# ============================================================
def run_collab(registry, *, thread_id, stage, ctx, model_choice, bindings, mode) -> str:
    leads, peers, subs = _split_roles(bindings)
    if mode == "discussion":
        return run_discussion(registry, thread_id=thread_id, stage=stage, ctx=ctx,
                              model_choice=model_choice, leads=leads, peers=peers)
    if mode == "dispatch":
        return run_dispatch(registry, thread_id=thread_id, stage=stage, ctx=ctx,
                            model_choice=model_choice, leads=leads, subs=subs)
    raise CollabError(f"未知 collab_mode '{mode}'")
