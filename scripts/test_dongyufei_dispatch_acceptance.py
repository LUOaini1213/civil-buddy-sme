import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packing_assistant.expert_turn import _dispatch_daily_md
from packing_assistant.office_job import tables_from_md

text = (
    "作业面：A区；"
    "用户事实：钢筋绑扎已完成；"
    "计划：明日进行模板安装；"
    "责任人：王工。"
)

r = _dispatch_daily_md(text)

assert r
assert "钢筋绑扎已完成" in r
assert "明日进行模板安装" in r
assert "# 调度日报草稿（AI 草稿 · 内部讨论）" in r
assert "不构成调度令" in r
assert "[A001]" in r
assert "不计算产值、完成率或进度偏差" in r
assert "submit_blocked=true" in r
sections = dict(tables_from_md(r))
actual = sections["4 当日实际"]
plan = sections["3 计划接口"]
actual_rows = [dict(zip(actual[0], row)) for row in actual[1:]]
plan_rows = [dict(zip(plan[0], row)) for row in plan[1:]]
assert any(row["用户施工状态 / 当日实绩记录（未核）"] == "钢筋绑扎已完成" for row in actual_rows)
assert any(row["用户计划节点 / 要求"] == "明日进行模板安装" for row in plan_rows)
assert all("模板安装" not in row["用户施工状态 / 当日实绩记录（未核）"] for row in actual_rows)
assert all(row["本轮完成核验"] == "未执行 / 待核" for row in actual_rows)

print("PASS dispatch acceptance")
print("user_fact =", "钢筋绑扎已完成" in r)
print("plan =", "明日进行模板安装" in r)
print("draft_guard =", "不构成调度令" in r)
print("missing_guard =", "[A001]" in r)
print("no_invention =", "不计算产值、完成率或进度偏差" in r)
