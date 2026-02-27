"""
Generate acceptance report by running Django checks and tests.

Usage:
    python scripts/generate_acceptance_report.py
    python scripts/generate_acceptance_report.py --output docs/ACCEPTANCE_REPORT_CUSTOM.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import re
import subprocess
import sys
from typing import Tuple


ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def run_cmd(cmd: list[str]) -> Tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout


def get_git_commit() -> str:
    code, out = run_cmd(["git", "rev-parse", "--short", "HEAD"])
    if code == 0:
        return out.strip()
    return "N/A"


def parse_test_summary(test_output: str) -> Tuple[str, int, int]:
    if "NO TESTS RAN" in test_output:
        return "NO_TESTS", 0, 0

    ran_match = re.search(r"Ran\s+(\d+)\s+tests?", test_output)
    ran_count = int(ran_match.group(1)) if ran_match else 0

    if "\nOK" in test_output or test_output.strip().endswith("OK"):
        return "OK", ran_count, 0

    failed = 0
    failed_match = re.search(r"FAILED\s+\((.*?)\)", test_output)
    if failed_match:
        payload = failed_match.group(1)
        for key in ("failures", "errors"):
            m = re.search(rf"{key}=(\d+)", payload)
            if m:
                failed += int(m.group(1))
    if failed == 0:
        failed = 1
    return "FAILED", ran_count, failed


def build_report(
    check_code: int,
    check_output: str,
    test_code: int,
    test_output: str,
) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit = get_git_commit()

    check_status = "PASS" if check_code == 0 else "FAIL"
    test_status, ran, failed = parse_test_summary(test_output)
    overall = "PASS" if check_status == "PASS" and test_code == 0 and test_status in ("OK", "NO_TESTS") else "FAIL"

    return f"""# 验收报告（自动生成）

## 基本信息
- 项目：宝宝周岁宴道具租赁系统
- 提交：`{commit}`
- 生成时间：{now}
- 脚本：`scripts/generate_acceptance_report.py`

## 自动化检查结果
- Django 系统检查（`manage.py check`）：**{check_status}**
- 自动化测试（`manage.py test`）：**{test_status}**
- 通过用例数：{max(ran - failed, 0)}
- 失败用例数：{failed}
- 执行用例总数：{ran}

## 手工验收清单（待勾选）
### 1. 订单全流程
- [ ] 新建订单
- [ ] 确认并进入待发货
- [ ] 标记发货
- [ ] 标记归还
- [ ] 标记完成
- [ ] 取消订单

### 2. 采购全流程
- [ ] 新建采购单（含明细）
- [ ] 标记下单
- [ ] 标记到货
- [ ] 确认入库（库存联动）

### 3. 转寄流程
- [ ] 从候选创建任务
- [ ] 任务完成
- [ ] 任务取消

### 4. API 验收
- [ ] 订单列表 API
- [ ] 订单状态流转 API
- [ ] 采购状态流转 API
- [ ] 转寄创建/列表 API

## 执行日志（check）
```text
{check_output.strip()}
```

## 执行日志（test）
```text
{test_output.strip()}
```

## 验收结论
- 自动化结论：**{overall}**
- 手工结论：`待填写`
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="", help="Optional custom output markdown path")
    args = parser.parse_args()

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    check_code, check_out = run_cmd([sys.executable, "manage.py", "check"])
    test_code, test_out = run_cmd([sys.executable, "manage.py", "test"])

    report = build_report(check_code, check_out, test_code, test_out)

    DOCS.mkdir(parents=True, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    default_out = DOCS / f"ACCEPTANCE_REPORT_{ts}.md"
    latest_out = DOCS / "ACCEPTANCE_REPORT_LATEST.md"
    output = pathlib.Path(args.output) if args.output else default_out

    output.write_text(report, encoding="utf-8")
    latest_out.write_text(report, encoding="utf-8")

    print(f"[OK] report generated: {output}")
    print(f"[OK] latest updated: {latest_out}")
    return 0 if (check_code == 0 and test_code == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
