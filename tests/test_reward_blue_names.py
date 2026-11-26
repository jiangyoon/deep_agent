"""chat_history 奖励蓝名提取的回归测试（纯函数，不依赖数据库）。

覆盖五态：正常提取去重 / 非奖励工具不附带 / 非法 JSON 降级 /
error 状态 / 展示截断不影响提取。蓝名随 tool_result 事件独立下发，
是前端「奖励证书名称标蓝」的唯一数据源，截断无关性是关键约束。
"""

import json

import pytest
from langchain_core.messages import ToolMessage

from career_agent.web.chat_history import tool_result_data


def reward_result(rows, extra_padding=0):
    """构造奖励工具的完整返回；extra_padding 在 JSON 前插入超长空白。"""
    payload = {"status": "found", "results": rows, "shared_rules": []}
    text = json.dumps(payload, ensure_ascii=False)
    return " " * extra_padding + text


def test_extracts_and_dedupes_blue_names():
    content = reward_result([
        {"certificate_name": "一级建造师", "blue_display_names": ["一级建造师注册证书"]},
        {"certificate_name": "组合", "blue_display_names": ["一级建造师", "信息安全工程师"]},
        {"certificate_name": "无蓝名行"},
    ])
    data = tool_result_data(ToolMessage(
        content=content, tool_call_id="t1", name="lookup_certificate_reward"))
    assert data["blueNames"] == ["一级建造师注册证书", "一级建造师", "信息安全工程师"]


def test_non_reward_tool_has_no_blue_names():
    data = tool_result_data(ToolMessage(
        content='{"status": "found", "results": [{"blue_display_names": ["一级建造师"]}]}',
        tool_call_id="t1", name="lookup_certificate_mapping"))
    assert "blueNames" not in data


def test_invalid_json_degrades_to_empty_list():
    data = tool_result_data(ToolMessage(
        content='{"status": "found"'[:8], tool_call_id="t1",
        name="lookup_certificate_reward"))
    assert data["blueNames"] == []


def test_error_status_yields_empty_list():
    data = tool_result_data(ToolMessage(
        content="boom", tool_call_id="t1", name="lookup_certificate_reward",
        status="error"))
    assert data["status"] == "error"
    assert data["blueNames"] == []


def test_extraction_survives_display_truncation():
    """蓝名出现在 2000 字截断点之后时，展示被截断、蓝名仍完整提取。"""
    rows = [
        {"certificate_name": "填充行", "blue_display_names": []},
        {"certificate_name": "咨询工程师（投资）", "blue_display_names": ["咨询工程师（投资）"]},
    ]
    content = reward_result(rows, extra_padding=3000)
    data = tool_result_data(ToolMessage(
        content=content, tool_call_id="t1", name="lookup_certificate_reward"))
    assert len(data["result"]) == 2001  # 展示截断到 2000 + 省略号
    assert data["result"].endswith("…")
    assert "咨询工程师（投资）" in data["blueNames"]
