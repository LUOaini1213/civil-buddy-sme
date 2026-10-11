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


def test_english_prepare_errors_localize_known_details_before_or_during_polling(runs):
    for r in runs["english_prepare_error_details"]["cases"]:
        _clean(r)
        assert "Local speech worker unavailable: ModuleNotFoundError" in r["after"]["status"]
        assert "本机识别子进程不可用" not in r["after"]["status"]
        assert any(word in r["after"]["status"] for word in ("browser recognition", "prepare local recognition again"))
        assert r["after"]["input"] == "原始资料.xlsx"
        assert r["log"]["gum"] == 0


def test_english_transcription_errors_localize_known_contract_without_changing_draft(runs):
    expected = ["Could not load the speech recognition engine: ImportError",
                "Could not load the speech recognition model: RuntimeError",
                "Local transcription failed: RuntimeError", "Speech model preparation failed: TimeoutError",
                "faster-whisper is not installed locally; the page will switch to browser speech recognition",
                "The local speech worker returned an invalid result",
                "Local transcription timed out; the transcription process was stopped",
                "The recording is too short", "The recording must not exceed 8 MB",
                "Prepare the local speech model before recording", "The previous recording is still being transcribed",
                "Each recording can last up to 20 seconds"]
    cases = runs["english_transcription_error_details"]["cases"]
    assert len(cases) == len(expected)
    for r, message in zip(cases, expected):
        _clean(r)
        assert message in r["after"]["status"]
        assert r["detail"] not in r["after"]["status"]
        assert r["after"]["input"] == "保留文件名.xlsx"
        assert r["log"]["events"] == []
        assert r["log"]["trackStops"] == 1


def test_unknown_backend_details_are_preserved_even_with_known_words_or_prefixes(runs):
    for r in runs["unknown_server_error_details_are_not_translated"]["cases"]:
        _clean(r)
        assert r["detail"] in r["after"]["status"]
        assert r["after"]["input"] == "原文"


def test_chinese_backend_error_and_exception_type_remain_original(runs):
    r = runs["chinese_server_error_details_remain_original"]
    _clean(r)
    assert r["after"]["status"] == "识别失败：" + r["detail"]
    assert r["after"]["input"] == "原文"


def test_english_ui_never_translates_chinese_transcripts_as_error_messages(runs):
    r = runs["english_ui_preserves_chinese_transcript_even_if_it_matches_an_error"]
    _clean(r)
    assert r["after"]["input"] == "原始图纸.xlsx 没有收到录音"
    assert r["switched"]["input"] == r["after"]["input"]
    assert "never sends automatically" in r["after"]["status"]
    assert r["log"]["events"] == ["input:input"]


def test_english_timeout_keeps_draft_and_aborts_without_sending(runs):
    r = runs["english_request_timeout_keeps_draft_and_releases_recording"]
    _clean(r)
    uploads = [request for request in r["log"]["requests"] if request["url"] == "/api/asr"]
    assert len(uploads) == 1 and uploads[0]["aborted"]
    assert r["after"]["status"] == "Transcription failed: Transcription timed out"
    assert r["after"]["input"] == "原始图纸.xlsx"
    assert r["after"]["label"] == "Voice input"
    assert r["log"]["trackStops"] == 1 and r["log"]["events"] == []


def test_english_locale_sets_local_decoder_hint_and_keeps_transcript_as_draft(runs):
    r = runs["english_server_hint_and_draft"]
    _clean(r)
    uploads = [request for request in r["log"]["requests"] if request["url"] == "/api/asr"]
    assert len(uploads) == 1 and uploads[0]["headers"]["X-Civil-ASR-Language"] == "en"
    assert r["after"]["input"] == "Review panel A12"
    assert r["after"]["label"] == "Voice input"
    assert "never sends automatically" in r["after"]["status"]


def test_english_locale_sets_browser_language_and_explains_consent(runs):
    r = runs["english_browser_hint_and_draft"]
    _clean(r)
    assert r["lang"] == "en-US"
    assert "Google" in r["log"]["confirmText"] and "Continue?" in r["log"]["confirmText"]
    assert r["log"]["order"].index("confirm") < r["log"]["order"].index("recognition")
    assert r["after"]["input"] == "Review panel A12"
    assert "never sends automatically" in r["after"]["status"]


def test_language_event_aborts_local_request_and_ignores_old_result(runs):
    r = runs["language_switch_cancels_server_and_preserves_typed_draft"]
    _clean(r)
    uploads = [request for request in r["log"]["requests"] if request["url"] == "/api/asr"]
    assert [request["headers"]["X-Civil-ASR-Language"] for request in uploads] == ["zh", "en"]
    assert uploads[0]["aborted"] and not uploads[1]["aborted"]
    assert f'/api/asr/{uploads[0]["headers"]["X-Civil-ASR-ID"]}/cancel' in r["log"]["fetches"]
    for snapshot in (r["switched"], r["settled"]):
        assert snapshot["input"] == "原始图纸.xlsx"
        assert snapshot["interim"] == "" and snapshot["label"] == "Voice input"
        assert "pending voice input was cancelled" in snapshot["status"]
    assert r["after"]["input"] == "原始图纸.xlsx New English note"
    assert r["log"]["events"] == ["input:input"]
    assert r["log"]["trackStops"] == 2


def test_language_event_aborts_browser_and_ignores_old_callbacks(runs):
    r = runs["language_switch_cancels_browser_and_preserves_typed_draft"]
    _clean(r)
    assert r["oldLang"] == "zh-CN" and r["newLang"] == "en-US"
    assert r["log"]["recAborts"] == 1
    for snapshot in (r["switched"], r["settled"]):
        assert snapshot["input"] == "原始图纸.xlsx"
        assert snapshot["interim"] == "" and snapshot["label"] == "Voice input"
        assert "pending voice input was cancelled" in snapshot["status"]
    assert r["after"]["input"] == "原始图纸.xlsx New English note"
    assert r["log"]["events"] == ["input:input"]


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


def test_nothing_available_keeps_a_recovery_check_accessible(runs):
    r = runs["nothing_available"]
    _clean(r)
    assert r["after"]["disabled"] is False and "不可用" in r["after"]["status"]


@pytest.fixture(scope="module")
def recovery_runs() -> dict:
    """Drive additional cases through the same fake-page API, without editing it."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = r'''
const fs = require("fs"), vm = require("vm");
const harness = fs.readFileSync(process.argv[3], "utf8");
const context = { require, console, Event, EventTarget, AbortController, Promise,
  process: { argv: ["node", "voice-recovery", process.argv[2]] } };
vm.createContext(context);
vm.runInContext(harness.slice(0, harness.indexOf("const READY =")), context);
const makePage = context.makePage;
const READY = { available: true, state: "ready", supports_cancel: true };
const result = {};
(async () => {
  for (const locale of ["zh-CN", "en"]) {
    let prepared = false, recovered = false;
    const page = makePage({ locale, speech: true, consent: false, initialText: "retained draft", fetch: (url) => {
      if (url.endsWith("/prepare")) { prepared = true; return [200, { state: "loading" }]; }
      return [200, recovered ? READY : { available: true, state: prepared ? "failed" : "missing", load_error: "模型准备失败：RuntimeError" }];
    } });
    await page.tap(); await page.advance(2500);
    const failed = page.snap(), firstCount = page.log.fetches.length;
    recovered = true; await page.tap();
    result[locale + "-recovered"] = { failed, firstCount, after: page.snap(), log: page.log };
    page.cancel();

    let posts = 0;
    const retry = makePage({ locale, initialText: "retained draft", fetch: (url) => {
      if (url.endsWith("/prepare")) { posts++; return [200, { state: "loading" }]; }
      return [200, posts ? READY : { available: true, state: "failed", load_error: "模型准备失败：RuntimeError" }];
    } });
    await retry.tap(); const pending = retry.snap(); await retry.advance(2500);
    result[locale + "-initial-failed"] = { posts, pending, after: retry.snap(), log: retry.log };
    retry.cancel();

    let repeatPosts = 0;
    const repeat = makePage({ locale, initialText: "retained draft", fetch: (url) => {
      if (url.endsWith("/prepare")) { repeatPosts++; return [200, { state: "loading" }]; }
      return [200, { available: true, state: "failed", load_error: "模型准备失败：RuntimeError" }];
    } });
    await repeat.tap(); await repeat.advance(2500); await repeat.tap(); await repeat.advance(2500);
    result[locale + "-repeat-failed"] = { posts: repeatPosts, after: repeat.snap(), log: repeat.log };

    let available = false;
    const absent = makePage({ locale, initialText: "retained draft", fetch: () => available ? [200, READY] : [503, {}] });
    await absent.tap(); const unavailable = absent.snap(); available = true; await absent.tap();
    result[locale + "-unavailable-recovery"] = { unavailable, after: absent.snap(), log: absent.log };
    absent.cancel();

    let local = false;
    const declined = makePage({ locale, speech: true, consent: false, initialText: "retained draft", fetch: () => local ? [200, READY] : [503, {}] });
    await declined.tap(); const refusal = declined.snap(); local = true; await declined.tap();
    result[locale + "-decline-recovery"] = { refusal, after: declined.snap(), log: declined.log };
    declined.cancel();

    let prep = 0;
    const late = makePage({ locale, initialText: "retained draft", fetch: (url, init, clock) => {
      if (url.endsWith("/prepare")) { prep++; return clock.wait(5000).then(() => [200, { state: "ready" }]); }
      return [200, { available: true, state: "cached" }];
    } });
    await late.tap(); await late.tap(); const cancelled = late.snap(); await late.advance(6000);
    result[locale + "-late-cancel"] = { prep, cancelled, after: late.snap(), log: late.log };

    let fallbackPosts = 0;
    const fallback = makePage({ locale, speech: true, consent: false, initialText: "retained draft", fetch: (url) => {
      if (url.endsWith("/prepare")) { fallbackPosts++; return [200, { state: "loading" }]; }
      return [200, fallbackPosts >= 2 ? READY : { available: true, state: "failed", load_error: "模型准备失败：RuntimeError" }];
    } });
    await fallback.tap(); await fallback.advance(2500);
    const beforeConsent = fallback.snap(); await fallback.tap(); const declinedFallback = fallback.snap();
    await fallback.tap(); await fallback.advance(2500);
    result[locale + "-prepare-browser-declined-retry"] = { posts: fallbackPosts, beforeConsent, declinedFallback, after: fallback.snap(), log: fallback.log };
    fallback.cancel();

    const accepted = makePage({ locale, speech: true, initialText: "retained draft", fetch: (url) => url.endsWith("/prepare")
      ? [200, { state: "loading" }] : [200, { available: true, state: "failed", load_error: "模型准备失败：RuntimeError" }] });
    await accepted.tap(); await accepted.advance(2500); const beforeBrowser = accepted.snap(); await accepted.tap();
    accepted.speech().onresult({ resultIndex: 0, results: [Object.assign([{ transcript: "review panel" }], { isFinal: true })] });
    await accepted.tap(50);
    result[locale + "-prepare-browser-consented"] = { beforeBrowser, after: accepted.snap(), log: accepted.log };
  }
  process.stdout.write(JSON.stringify(result));
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
'''
    out = subprocess.run([node, "-", str(VOICE_JS), str(HERE / "voice_harness.js")],
                         input=script, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_failed_preparation_next_click_reprobes_ready_local_service(recovery_runs):
    for locale in ("zh-CN", "en"):
        result = recovery_runs[locale + "-recovered"]
        _clean(result)
        assert len(result["log"]["fetches"]) > result["firstCount"]
        assert result["log"]["gum"] == 1 and result["log"]["recStarts"] == 0
        assert result["log"]["confirms"] == 0 and result["after"]["input"] == "retained draft"


def test_initial_failed_state_is_explicitly_prepared_before_recording(recovery_runs):
    for locale in ("zh-CN", "en"):
        result = recovery_runs[locale + "-initial-failed"]
        _clean(result)
        assert result["posts"] == 1 and result["log"]["gum"] == 1
        assert result["pending"]["btn"] in ("取消", "Cancel")
        assert result["log"]["order"].index("fetch /api/asr/prepare") < result["log"]["order"].index("getUserMedia")
        assert result["after"]["input"] == "retained draft"


def test_repeated_prepare_failure_remains_retryable_without_cloud_or_audio(recovery_runs):
    for locale in ("zh-CN", "en"):
        result = recovery_runs[locale + "-repeat-failed"]
        _clean(result)
        assert result["posts"] == 2 and result["log"]["gum"] == 0
        assert result["log"]["confirms"] == result["log"]["recStarts"] == 0
        assert result["after"]["input"] == "retained draft" and result["after"]["btn"] == ""


def test_unavailable_service_can_recover_without_page_reload(recovery_runs):
    for locale in ("zh-CN", "en"):
        result = recovery_runs[locale + "-unavailable-recovery"]
        _clean(result)
        assert result["unavailable"]["disabled"] is False and result["log"]["gum"] == 1
        assert result["after"]["input"] == "retained draft"


def test_browser_refusal_does_not_claim_missing_installation_and_allows_local_recovery(recovery_runs):
    for locale in ("zh-CN", "en"):
        result = recovery_runs[locale + "-decline-recovery"]
        _clean(result)
        assert "requirements" not in result["refusal"]["status"]
        assert "install" not in result["refusal"]["status"] and "安装" not in result["refusal"]["status"]
        assert result["log"]["confirms"] == 1 and result["log"]["recStarts"] == 0
        assert result["log"]["gum"] == 1 and result["after"]["input"] == "retained draft"


def test_cancelled_preparation_late_response_never_starts_audio_or_changes_draft(recovery_runs):
    for locale in ("zh-CN", "en"):
        result = recovery_runs[locale + "-late-cancel"]
        _clean(result)
        assert result["prep"] == 1 and result["log"]["gum"] == result["log"]["recStarts"] == 0
        assert result["after"] == result["cancelled"]
        assert result["after"]["input"] == "retained draft"
        assert result["log"]["requests"][-1]["aborted"] is True


def test_failed_prepare_browser_refusal_keeps_explicit_local_retry(recovery_runs):
    for locale in ("zh-CN", "en"):
        result = recovery_runs[locale + "-prepare-browser-declined-retry"]
        _clean(result)
        assert result["posts"] == 2 and result["log"]["confirms"] == 1
        assert result["log"]["recStarts"] == 0 and result["log"]["gum"] == 1
        assert result["declinedFallback"]["input"] == result["after"]["input"] == "retained draft"
        assert "requirements" not in result["declinedFallback"]["status"]


def test_failed_prepare_browser_fallback_requires_consent_and_only_fills_draft(recovery_runs):
    for locale in ("zh-CN", "en"):
        result = recovery_runs[locale + "-prepare-browser-consented"]
        _clean(result)
        assert result["log"]["gum"] == 0 and result["log"]["recStarts"] == 1
        assert result["log"]["order"].index("confirm") < result["log"]["order"].index("recognition")
        assert result["after"]["input"] == "retained draft review panel"
        assert result["log"]["events"] == ["input:input"]
