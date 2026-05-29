"""Tool hooks —— 安全護欄，全部只 import plugin_api。

- DenyProtectedBranchHook：pre_run 掃 argv，若對受保護分支做 push / 強制 branch / checkout，
  raise HookAbort 拒絕該次 run（host orchestrator 記為「被擋」）。
- RedactSecretsHook：on_log_chunk 把串流輸出裡的疑似秘密（PAT / Bearer / key=value）塗黑，
  避免 token 落進持久化的 log 或前端畫面。
"""
from __future__ import annotations

import re

from plugin_api import HookAbort, ToolHook


class DenyProtectedBranchHook(ToolHook):
    name = "deny_protected_branch"
    PROTECTED = ("main", "master", "release", "production")

    def pre_run(self, runner_name: str, argv: list[str], env: dict[str, str]):
        joined = " ".join(argv)
        if "push" not in joined and "checkout" not in joined and "branch" not in joined:
            return None  # 與 git 分支操作無關，直接放行
        for br in self.PROTECTED:
            b = re.escape(br)
            dangerous = (
                re.search(rf"\bpush\b.*\b{b}\b", joined)              # git push ... main
                or re.search(rf"--branch[ =]{b}\b", joined)           # --branch main
                or re.search(rf"\bcheckout\b.*-B?\s+{b}\b", joined)   # checkout -B main
                or re.search(rf"\bbranch\b.*\b-f\b.*\b{b}\b", joined) # branch -f main
            )
            if dangerous:
                raise HookAbort(self.name, f"拒絕對受保護分支 '{br}' 的危險操作")
        return None


class RedactSecretsHook(ToolHook):
    name = "redact_secrets"
    _MASK = "[REDACTED]"
    # (pattern, replacement)。由具體到一般；key=value 保留 key、只塗黑 value。
    _RULES = (
        (re.compile(r"ghp_[A-Za-z0-9]{16,}"), _MASK),                  # GitHub PAT (classic)
        (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), _MASK),          # GitHub fine-grained PAT
        (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer " + _MASK),
        (re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b(\s*[=:]\s*)(\S+)"),
         r"\1\2" + _MASK),                                             # token=xxx → token=[REDACTED]
    )

    def on_log_chunk(self, runner_name: str, chunk: str):
        redacted = chunk
        for pat, repl in self._RULES:
            redacted = pat.sub(repl, redacted)
        return redacted
