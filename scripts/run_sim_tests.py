#!/usr/bin/env python3
# ============================================================================
# run_sim_tests.py — 统一仿真编排器（CI 仿真测试层核心）
#
# 职责：
#   1. 发现场景清单（scenarios.yaml）
#   2. 按 filter 正则 + shard 分片筛选
#   3. 逐个调用仿真命令（Gazebo 无头 / Isaac Sim），解析其输出的指标 JSON
#   4. 与基线对比，超出容差即判失败（确定性回归）
#   5. 产出 junit.xml（GitLab/Jenkins 测试报告）与 metrics.json（趋势/看板）
#
# 用法：
#   python3 run_sim_tests.py --scenario-dir sim/scenarios \
#       --seed 42 --tolerance 0.05 --out-dir sim_out
#   python3 run_sim_tests.py --self-test        # 无需仿真器，冒烟验证流水线
#
# 场景命令约定：仿真脚本把指标以单行 JSON 打印到 stdout，前缀为：
#   SIM_METRICS:{"tracking_error":0.031,"collisions":0,...}
# ============================================================================
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

METRIC_MARKER = "SIM_METRICS:"


def discover_scenarios(scenario_dir: Path) -> list[dict]:
    """从 scenarios.yaml 读取场景定义。"""
    manifest = scenario_dir / "scenarios.yaml"
    if manifest.exists():
        try:
            import yaml  # type: ignore
        except ImportError:
            # 清单存在却解析不了属于配置错误，直接终止而非静默清空场景
            sys.exit(f"[error] {manifest} 存在但环境缺少 PyYAML，无法解析场景清单")
        data = yaml.safe_load(manifest.read_text()) or {}
        if isinstance(data, dict):
            return data.get("scenarios") or []
        return data or []
    json_manifest = scenario_dir / "scenarios.json"
    if json_manifest.exists():
        data = json.loads(json_manifest.read_text()) or {}
        if isinstance(data, dict):
            return data.get("scenarios") or []
        return data or []
    return []


def parse_metrics(stdout: str) -> dict:
    """从仿真 stdout 解析最后一个 SIM_METRICS: 行。"""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith(METRIC_MARKER):
            try:
                return json.loads(line[len(METRIC_MARKER):])
            except json.JSONDecodeError:
                break
    return {}


def compare(metrics: dict, baselines: dict, tolerance: float) -> tuple[bool, list[str]]:
    """逐项对比，返回 (是否通过, 失败原因列表)。"""
    ok = True
    reasons: list[str] = []
    for name, baseline in baselines.items():
        if name not in metrics:
            ok = False
            reasons.append(f"缺少指标 {name}")
            continue
        val = float(metrics[name])
        if abs(val - float(baseline)) > float(tolerance) * float(baseline) + 1e-9:
            ok = False
            reasons.append(
                f"{name}={val:.4f} 超出基线 {baseline} 的容差 {tolerance*100:.1f}%"
            )
    return ok, reasons


def run_scenario(scn: dict, seed: str, out_dir: Path) -> dict:
    """执行单个场景，返回结果字典。"""
    name = scn.get("id", "<unknown>")
    started = datetime.now(timezone.utc)
    try:
        # 用显式替换，避免命令中的 JSON 花括号被 .format() 误解析
        cmd = scn["run"].replace("{seed}", seed).replace("{out_dir}", str(out_dir))
        baselines = scn.get("baselines", {})
        tolerance = float(scn.get("tolerance", 0.05))
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=scn.get("timeout", 600)
        )
    except (KeyError, ValueError) as e:
        return {"id": name, "passed": False, "reasons": [f"场景定义无效或缺少字段: {e}"],
                "metrics": {}}
    except subprocess.TimeoutExpired:
        return {"id": name, "passed": False, "reasons": ["执行超时"], "metrics": {}}

    metrics = parse_metrics(proc.stdout)
    try:
        passed, reasons = compare(metrics, baselines, tolerance)
    except (TypeError, ValueError) as e:
        # 指标为脏数据：记为该场景失败，而非整条流水线崩溃
        passed, reasons = False, [f"指标解析/比较失败: {e}"]
    if proc.returncode != 0 and not scn.get("ignore_rc", False):
        passed = False
        reasons.append(f"进程退出码 {proc.returncode}")

    return {
        "id": name,
        "passed": passed,
        "reasons": reasons,
        "metrics": metrics,
        "duration_ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
    }


def write_junit(results: list[dict], path: Path) -> None:
    suites = ET.Element("testsuites")
    suite = ET.SubElement(suites, "testsuite",
                          name="sim-regression", tests=str(len(results)),
                          failures=str(sum(0 if r["passed"] else 1 for r in results)))
    for r in results:
        case = ET.SubElement(suite, "testcase", name=r["id"], classname="sim",
                             time=str(r.get("duration_ms", 0) / 1000.0))
        if not r["passed"]:
            fail = ET.SubElement(case, "failure", message="; ".join(r["reasons"]))
            fail.text = "; ".join(r["reasons"])
    path.write_text(ET.tostring(suites, encoding="unicode"))


def write_metrics(results: list[dict], path: Path) -> None:
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r["passed"]),
        },
        "scenarios": [
            {"id": r["id"], "passed": r["passed"], "metrics": r["metrics"]}
            for r in results
        ],
    }
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))


def self_test(out_dir: Path) -> list[dict]:
    """无需仿真器的内置冒烟场景，用于验证流水线本身。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    scenarios = [
        {"id": "self.pass", "run": "echo 'SIM_METRICS:{\"err\":0.0205}'",
         "baselines": {"err": 0.02}, "tolerance": 0.05},
        {"id": "self.fail", "run": "echo 'SIM_METRICS:{\"err\":0.10}'",
         "baselines": {"err": 0.02}, "tolerance": 0.05},
    ]
    return [run_scenario(s, "42", out_dir) for s in scenarios]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario-dir", default="sim/scenarios")
    ap.add_argument("--seed", default="42")
    ap.add_argument("--tolerance", type=float, default=0.05)
    ap.add_argument("--out-dir", default="sim_out")
    ap.add_argument("--filter", default="")
    ap.add_argument("--shard-index", type=int, default=0)
    ap.add_argument("--shard-count", type=int, default=1)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.self_test:
        results = self_test(out_dir)
    else:
        scenarios = discover_scenarios(Path(args.scenario_dir))
        if args.filter:
            pat = re.compile(args.filter)
            scenarios = [s for s in scenarios if pat.search(s["id"])]
        # 分片：round-robin 分配，保证并行可复现
        scenarios = [s for i, s in enumerate(scenarios) if i % args.shard_count == args.shard_index]
        if not scenarios:
            # 门禁语义：跑不到任何场景视为配置错误，必须红，防止静默放行
            print("[error] 无匹配场景（检查 scenario-dir / filter / shard 配置与清单内容）",
                  file=sys.stderr)
            write_junit([], out_dir / "junit.xml")
            write_metrics([], out_dir / "metrics.json")
            return 2
        results = [run_scenario(s, args.seed, out_dir) for s in scenarios]

    write_junit(results, out_dir / "junit.xml")
    write_metrics(results, out_dir / "metrics.json")

    if args.self_test:
        # self.fail 为预期失败：实际结果与预期一致才算自检通过
        expected = {"self.pass": True, "self.fail": False}
        mismatched = [r["id"] for r in results if r["passed"] != expected[r["id"]]]
        if mismatched:
            print(f"[self-test] 结果与预期不符: {mismatched}", file=sys.stderr)
            return 1
        print("[self-test] OK")
        return 0

    failed = [r for r in results if not r["passed"]]
    print(f"[done] 场景 {len(results)} 个，失败 {len(failed)} 个")
    for r in failed:
        print(f"  FAIL {r['id']}: {'; '.join(r['reasons'])}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
