#!/usr/bin/env python3
# ============================================================================
# ci_tool_link.py — CI 与协作系统的「回写联动」桥接
#
# 作用：把仿真结果（junit.xml / metrics.json）推回代码评审与协作系统，
#       实现工具联动闭环：仿真通过率 → MR 评论 / 状态 Check / 看板。
#
# 当前支持：
#   --provider gitlab   回写 GitLab Merge Request 评论 + 流水线状态
#   --provider jenkins  仅打印联动意图（Jenkins 侧多用原生 junit() 即可）
#
# 用法（GitLab）：
#   python3 ci_tool_link.py --provider gitlab \
#       --project 123 --mr-iid 45 \
#       --metrics sim_out/metrics.json --junit sim_out/junit.xml \
#       --token $GITLAB_TOKEN
# ============================================================================
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def render_comment(metrics: dict, junit_path: str) -> str:
    s = metrics.get("summary", {})
    total = s.get("total", 0)
    passed = s.get("passed", 0)
    lines = [
        "## 仿真回归报告 (Sim CI)",
        "",
        f"- 场景总数：**{total}**，通过：**{passed}**，失败：**{total - passed}**",
        "",
        "| 场景 | 结果 | 关键指标 |",
        "|------|------|----------|",
    ]
    for sc in metrics.get("scenarios", []):
        icon = "✅" if sc["passed"] else "❌"
        m = " ".join(f"{k}={v}" for k, v in sc.get("metrics", {}).items())
        lines.append(f"| {sc['id']} | {icon} | {m} |")
    lines += ["", f"测试明细见 JUnit 报告：`{junit_path}`"]
    return "\n".join(lines)


def post_gitlab(token: str, project: str, mr_iid: str, body: str) -> bool:
    try:
        import requests
    except ImportError:
        print("[error] 需要 requests: pip install requests", file=sys.stderr)
        return False
    base = os.environ.get("CI_API_V4_URL", "https://gitlab.com/api/v4")
    headers = {"PRIVATE-TOKEN": token}
    url = f"{base}/projects/{project}/merge_requests/{mr_iid}/notes"
    r = requests.post(url, headers=headers, json={"body": body}, timeout=30)
    if r.status_code >= 400:
        print(f"[error] GitLab 评论失败 {r.status_code}: {r.text}", file=sys.stderr)
        return False
    print("[ok] 已回写 GitLab MR 评论")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["gitlab", "jenkins"], required=True)
    ap.add_argument("--project")
    ap.add_argument("--mr-iid")
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--junit", required=True)
    ap.add_argument("--token", default=os.environ.get("GITLAB_TOKEN"))
    args = ap.parse_args()

    metrics_path = Path(args.metrics)
    if not metrics_path.exists():
        print(f"[error] 指标文件不存在: {args.metrics}（上游仿真产物未传递?）", file=sys.stderr)
        return 2
    metrics = json.loads(metrics_path.read_text())
    comment = render_comment(metrics, args.junit)

    if args.provider == "gitlab":
        if not args.token:
            print("[error] 缺少 GitLab token", file=sys.stderr)
            return 2
        ok = post_gitlab(args.token, args.project, args.mr_iid, comment)
        return 0 if ok else 1

    # jenkins：原生 junit() 已处理报告，这里仅输出联动摘要供日志审计
    print("[jenkins] 联动通过原生 junit()/archiveArtifacts 完成；摘要：")
    print(comment)
    return 0


if __name__ == "__main__":
    sys.exit(main())
