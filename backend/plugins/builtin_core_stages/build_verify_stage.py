"""build_verify stage：在 implement 的快照上跑專案的 build 指令，回報編譯成功/失敗。

這是個**非 LLM** stage —— generate handler 直接 subprocess 跑 build（不經 model adapter）：
  - 目標目錄 = `repo_workspace.project_clone_dir(thread_id)`（implement 寫碼的快照/clone；local 與
    remote 都寫這。**不**用一般 stage 的 ctx.workspace_dir——那在 local 模式是唯讀原始路徑、非快照）。
  - build 指令 + env script 來自 per-project 設定（projects.build_command / build_env_script）。
  - 編不過 → 往 runner 記一筆 fail validation（run.record_validations），engine 的 has_fail 機制把
    stage 標成 needs_revision，與 judge-fail 同一條路、UI 一致；build log 一律存進 artifact。

用法：change_request（談）→ Implement → PR（把碼寫進快照）→ build_verify（編快照、回報）。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from plugin_api import HarnessValidationOutcome, StageContext, StageResult, StageSpec
from plugin_api.harness import SEVERITY_FAIL

# 隔離鐵則：plugin 只 import plugin_api。snapshot 路徑與 build 設定皆由 host 經 ctx 注入：
#   - ctx.workspace_dir：host 對 requires=("impl_workspace",) 備好的 implement 快照/clone 路徑
#   - ctx.metadata["build_command"] / ["build_env_script"]：host 從專案設定注入
_BUILD_TIMEOUT = 1800   # 單次 build 上限（秒）
_LOG_CAP = 50_000       # build log head/tail 各保留上限（避免塞爆 artifact）

# 剝掉 ANSI 控制碼：toolchain.cmake 寫死 -fdiagnostics-color=always，gcc 對 pipe 也吐顏色碼，
# 這些控制字元會讓 JSON 回應序列化出問題、且在前端顯示成亂碼。存 artifact 前一律清掉。
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _truncate(text: str, cap: int = _LOG_CAP) -> str:
    if len(text) <= 2 * cap:
        return text
    return f"{text[:cap]}\n\n…（log 過長已截斷，中間略 {len(text) - 2 * cap} 字）…\n\n{text[-cap:]}"


def _build_verify_generate(ctx: StageContext, run) -> StageResult:
    build_cmd = (ctx.metadata.get("build_command") or "").strip()
    env_script = (ctx.metadata.get("build_env_script") or "").strip()
    snapshot = Path(ctx.workspace_dir) if ctx.workspace_dir else None

    if not build_cmd:
        run.record_validations([HarnessValidationOutcome(
            validator="build_verify.config", severity=SEVERITY_FAIL,
            message="未設定 build 指令",
            fix_hint="在專案設定填 build_command（如 cmake --build . --target flash_nn）")])
        return StageResult(artifact="# Build Verify\n\nStatus: SKIPPED —— 未設定 build 指令（請到專案設定填 build_command）。")

    if not (snapshot and snapshot.exists() and (snapshot / ".git").exists()):
        run.record_validations([HarnessValidationOutcome(
            validator="build_verify.workspace", severity=SEVERITY_FAIL,
            message="找不到 implement 快照",
            fix_hint="先按 Implement 把程式碼寫進快照，再驗證編譯")])
        return StageResult(
            artifact=f"# Build Verify\n\nStatus: NO CODE —— 找不到 implement 快照（{snapshot}）。請先跑 Implement。")

    # 在快照上跑 build；可選先 source env script 讓 toolchain（arm-none-eabi-gcc 等）上 PATH。
    inner = f'source "{env_script}" && {build_cmd}' if env_script else build_cmd
    try:
        proc = subprocess.run(["bash", "-lc", inner], cwd=str(snapshot),
                              capture_output=True, text=True, timeout=_BUILD_TIMEOUT)
        exit_code = proc.returncode
        log = (proc.stdout or "")
        if proc.stderr:
            log += f"\n[stderr]\n{proc.stderr}"
    except subprocess.TimeoutExpired:
        exit_code, log = -1, f"[build_verify] 逾時（>{_BUILD_TIMEOUT}s）"
    except Exception as exc:  # noqa: BLE001 - 任何執行失敗都當編譯失敗回報，不上拋
        exit_code, log = -1, f"[build_verify] 執行失敗：{exc}"

    log = _strip_ansi(log)
    ok = exit_code == 0
    artifact = (
        f"# Build Verify\n\n"
        f"Status: {'SUCCESS' if ok else 'FAILED'} (exit {exit_code})\n"
        f"Dir: {snapshot}\n"
        f"Cmd: {build_cmd}\n"
        + (f"Env: {env_script}\n" if env_script else "")
        + f"\n## Build Log\n```\n{_truncate(log)}\n```\n"
    )
    if not ok:
        run.record_validations([HarnessValidationOutcome(
            validator="build_verify.compile", severity=SEVERITY_FAIL,
            message=f"編譯失敗（exit {exit_code}）",
            fix_hint="看上方 build log 修正後重跑 implement，再驗證編譯",
            detail={"exit_code": exit_code})])
    return StageResult(artifact=artifact, state_extra={"build_ok": ok, "exit_code": exit_code})


BUILD_VERIFY_STAGE = StageSpec(
    id="build_verify",
    label="Build Verify",
    description="在 implement 的快照上跑專案 build 指令、回報編譯成功/失敗（非 LLM；編不過 → stage needs_revision）。",
    telemetry_stage="deliver",
    requires=("impl_workspace",),   # host 備 implement 快照路徑進 ctx.workspace_dir + build 設定進 ctx.metadata
    generate=_build_verify_generate,
    artifact_key="build_verify",
)
