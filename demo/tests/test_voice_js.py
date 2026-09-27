"""voice.js behaviour, run in node against a fake page (voice_harness.js).

A string denylist cannot prove "never sends": a synthetic Enter keydown or a click on
#send reached through app.js's `$` would pass it. The harness instead hands voice.js
only the five elements it owns and records every other DOM reach, every event on the
textarea, and the order of consent vs. recognition.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
VOICE_JS = HERE.parent / "static" / "voice.js"


@pytest.fixture(scope="module")
def runs() -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    out = subprocess.run([node, str(HERE / "voice_harness.js"), str(VOICE_JS)],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert not data.get("__incomplete"), "a scenario hung waiting on a timer"
    for name, result in data.items():
        if not name.startswith("__"):
            assert "error" not in result, (name, result.get("error"))
    return data


def _clean(result: dict) -> None:
    log = result["log"]
    assert log["violations"] == [], log["violations"]
    assert all(e == "input:input" for e in log["events"]), log["events"]


def test_cancel_aborts_server_request_and_rejects_late_transcript(runs):
    r = runs["cancelled_server_result_is_ignored"]
    _clean(r)
    assert r["requestId"]
    assert f'/api/asr/{r["requestId"]}/cancel' in r["log"]["fetches"]
    assert r["after"]["input"] == "" and r["after"]["btn"] == ""


def test_cancel_while_mic_permission_pending_releases_late_stream(runs):
    r = runs["cancelled_mic_start_releases_late_stream"]
    _clean(r)
    assert r["log"]["recorders"] == 0 and r["log"]["trackStops"] == 1
    assert r["after"]["input"] == ""


def test_cancelled_browser_callbacks_cannot_fill_draft(runs):
    r = runs["cancelled_browser_result_is_ignored"]
    _clean(r)
    assert r["after"]["input"] == "" and r["after"]["btn"] == ""


def test_server_path_fills_the_box_and_never_sends(runs):
    r = runs["server_fill_never_send"]
    _clean(r)
    assert r["during"]["btn"].startswith("停止") and r["during"]["pressed"] == "true"
    after = r["after"]
    assert after["input"] == "先写的脚手架的连墙件"  # appended, no space between Chinese
    assert "不会自动发送" in after["status"] and after["focus"] == "input"
    assert after["btn"] == "" and after["label"] == "语音输入"  # idle: microphone icon only
    assert r["log"]["fetches"].count("/api/asr") == 1 and r["log"]["trackStops"] == 1


def test_second_click_while_the_mic_starts_opens_no_second_recorder(runs):
    r = runs["double_click_while_mic_starts"]
    _clean(r)
    assert r["log"]["gum"] == 1 and r["log"]["recorders"] == 1


def test_recording_stops_one_second_before_the_server_limit(runs):
    r = runs["auto_stop_one_second_early"]
    _clean(r)
    assert len(r["log"]["stopAt"]) == 1
    assert r["log"]["stopAt"][0] - r["log"]["recordAt"][0] == 19000
    assert r["after"]["input"] == "好"


def test_a_recorder_that_stops_by_itself_leaves_no_timer_behind(runs):
    r = runs["no_stale_timer_after_self_stop"]
    _clean(r)
    first, second = r["log"]["recordAt"]
    stale_fire = first + 19000  # when the first recording's auto-stop timer was due
    assert second < stale_fire < second + 18000  # it would have landed inside the second recording
    assert len(r["log"]["stopAt"]) == 1 and r["log"]["stopAt"][0] < second  # only the self-stop happened
    assert r["stillRecording"]["btn"] == "停止 0:18"


def test_model_is_prepared_before_any_audio_is_recorded(runs):
    r = runs["prepares_before_recording"]
    _clean(r)
    assert r["gumWhilePreparing"] == 0 and r["preparing"]["btn"] == "取消"
    order = r["log"]["order"]
    assert order.index("fetch /api/asr/prepare") < order.index("getUserMedia")
    assert r["after"]["btn"].startswith("停止")


def test_cancel_while_preparing_records_nothing(runs):
    r = runs["cancel_while_preparing"]
    _clean(r)
    assert r["log"]["gum"] == 0 and r["after"]["btn"] == "" and r["after"]["label"] == "语音输入"


def test_server_failure_falls_back_to_browser_only_after_consent(runs):
    r = runs["server_error_falls_back_to_browser_after_consent"]
    _clean(r)
    assert "浏览器识别" in r["afterError"]["status"]
    order = r["log"]["order"]
    assert order.index("confirm") < order.index("recognition")
    assert r["after"]["input"] == "基坑支护"


def test_declined_consent_starts_no_recognition_and_consent_precedes_audio(runs):
    r = runs["browser_consent_declined_then_given"]
    assert r["declined"]["log"]["recStarts"] == 0 and r["declined"]["log"]["violations"] == []
    _clean(r)
    assert "Google" in r["log"]["confirmText"]
    assert r["log"]["order"] == ["fetch /api/asr/status", "confirm", "recognition"]
    assert r["listening"]["status"].startswith("正在听") and r["after"]["input"] == "预应力张拉"


def test_browser_session_with_no_text_says_so(runs):
    r = runs["browser_ends_without_text"]
    _clean(r)
    assert r["after"]["status"] == "没有听清，请再说一次。" and r["after"]["btn"] == ""


def test_nothing_available_disables_the_button(runs):
    r = runs["nothing_available"]
    _clean(r)
    assert r["after"]["disabled"] is True and "不可用" in r["after"]["status"]
