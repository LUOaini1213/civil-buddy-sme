/* 语音输入（可选）。
   说完只把文字回填到输入框：不会自动发送，由人核对、改好后自己按发送。
   识别来源按顺序选：
     1) 本机识别：Python 工作台的 /api/asr（faster-whisper + 土木术语表），录音只在本机内存里处理。
        模型没准备好（第一次要下载约 460 MB）时先准备、准备好再录，不会让人白说一段；
     2) 浏览器自带识别：本机识别不可用或运行出错时才用（如 Rust 试用包）。Chrome 会把录音发到
        Google 的服务器，第一次使用前弹窗说明，同意后才开始；
     3) 两者都没有：按钮置灰并说明原因。
   一段录音最长 20 秒（后端 asr.MAX_SECONDS）：页面提前 1 秒自动停，后端另有 2 秒宽限并裁到 20 秒。 */
(function () {
  "use strict";
  const cbVoiceText = (source, values = {}) => window.CBI18n?.t(source, values) ?? String(source || "").replace(/\{(\w+)\}/g, (match, key) => values[key] ?? match);


  const MAX_SECONDS = 20;
  const MAX_MS = (MAX_SECONDS - 1) * 1000;
  const POLL_MS = 2000;
  const REQUEST_TIMEOUT_MS = 90000;
  const CONSENT_KEY = "cb_voice_browser_consent_v1";
  const btn = document.getElementById("btnVoice");
  const input = document.getElementById((btn && btn.dataset.inputId) || "input");
  const statusEl = document.getElementById("voiceStatus");
  const interimEl = document.getElementById("voiceInterim");
  const labelEl = document.getElementById("voiceLabel"); // 麦克风图标旁的文字：空闲时为空，只显示图标
  if (!btn || !input) return;

  const BrowserRecognition = window.SpeechRecognition || window.webkitSpeechRecognition || null;
  let mode = null; // 只缓存 "server"；浏览器 / 不可用每次点击重新探测，装好本机识别后下一次就会用上
  let serverBroken = false; // 本机识别在运行中出过错（5xx）：本页不再用，改走浏览器
  let phase = "idle"; // idle | starting | preparing | recording | transcribing
  let recorder = null;
  let stream = null;
  let chunks = [];
  let startedAt = 0;
  let tick = null;
  let stopTimer = null;
  let recognition = null;
  let generation = 0;
  let activeRequest = null;
  let supportsCancel = false;
  const alive = (token) => token === generation;

  function cancel(reason) {
    const wasActive = phase !== "idle" || activeRequest || recorder || recognition;
    generation += 1;
    const pending = activeRequest; activeRequest = null;
    if (recorder) { recorder.onstop = null; recorder.ondataavailable = null; if (recorder.state !== "inactive") recorder.stop(); recorder = null; }
    if (recognition) { const old = recognition; recognition = null; old.onend = old.onresult = old.onerror = null; if (old.abort) old.abort(); else old.stop(); }
    releaseMic(); chunks = []; interim(""); setPhase("idle");
    if (pending) {
      pending.abort.abort();
      if (supportsCancel) fetch("/api/asr/" + encodeURIComponent(pending.id) + "/cancel", { method: "POST", keepalive: true }).catch(() => {});
    }
    say(wasActive ? reason || (pending && !supportsCancel ? cbVoiceText("已取消回填；此服务未声明后台转写取消能力。") : cbVoiceText("已取消语音草稿。")) : "", "");
  }
  window.CivilBuddyVoice = { cancel };

  function say(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text || "";
    statusEl.dataset.kind = kind || "";
  }

  function interim(text) {
    if (interimEl) interimEl.textContent = text || "";
  }

  function paint() {
    const busy = phase === "starting" || phase === "transcribing";
    let text = "";
    if (phase === "recording") {
      const secs = Math.floor((Date.now() - startedAt) / 1000);
      text = cbVoiceText("停止 0:") + String(secs).padStart(2, "0");
      btn.setAttribute("aria-label", cbVoiceText("停止录音"));
    } else if (phase === "preparing") {
      text = cbVoiceText("取消");
      btn.setAttribute("aria-label", cbVoiceText("取消等待识别模型"));
    } else {
      text = phase === "transcribing" ? cbVoiceText("取消识别") : "";
      btn.setAttribute("aria-label", phase === "transcribing" ? cbVoiceText("取消语音识别") : cbVoiceText("语音输入"));
    }
    // 只改图标旁的文字，不碰按钮里的 SVG 图标
    if (labelEl) labelEl.textContent = text;
    btn.setAttribute("aria-pressed", phase === "recording" ? "true" : "false");
    btn.setAttribute("aria-busy", busy ? "true" : "false");
    // 忙的时候用 aria-disabled 而不是 disabled：按钮不失焦，点击由 phase 挡住
    btn.setAttribute("aria-disabled", phase === "starting" ? "true" : "false");
    btn.classList.toggle("is-rec", phase === "recording");
    btn.disabled = mode === "none";
  }

  /* 所有计时器都在这里收放：离开 recording 的每条路径都经过 setPhase，不会留下旧的 20 秒计时器 */
  function setPhase(next) {
    phase = next;
    clearInterval(tick);
    clearTimeout(stopTimer);
    tick = null;
    stopTimer = null;
    if (next === "recording") {
      startedAt = Date.now();
      tick = setInterval(paint, 500);
      stopTimer = setTimeout(stopRecording, MAX_MS);
    }
    paint();
  }

  /* 回填：接在已有文字后面，只触发 input 事件，让 app.js 的自适应高度与发送键状态跟着更新 */
  function fill(text) {
    const words = String(text || "").trim();
    if (!words) {
      say(cbVoiceText("没有听清，请再说一次。"), "warn");
      return false;
    }
    const current = input.value;
    // 只在英文或数字后面补空格；中文句子之间直接接上
    input.value = /[A-Za-z0-9]$/.test(current) ? current + " " + words : current + words;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    return true;
  }

  async function serverStatus() {
    try {
      const response = await fetch("/api/asr/status");
      if (response.ok) { const info = await response.json(); supportsCancel = info.supports_cancel === true; return info; }
    } catch (_) {
      /* 服务没有这个接口（Rust 试用包）或断网 */
    }
    return null;
  }

  async function detectMode() {
    if (mode === "server" && !serverBroken) return mode;
    const info = serverBroken ? null : await serverStatus();
    if (info && info.available) return (mode = "server");
    return (mode = BrowserRecognition ? "browser" : "none");
  }

  /* 本机模型准备好了才开始录音：第一次要下载，下载时不录，免得说完了才发现识别不了 */
  async function ensureReady(token) {
    let info = await serverStatus();
    if (!alive(token)) return false;
    if (!info || !info.available) return false;
    if (info.state === "ready") return true;
    if (info.state === "failed") {
      serverBroken = true;
      mode = null;
      say(cbVoiceText("本机识别模型准备失败（") + (info.load_error || cbVoiceText("原因未知")) + cbVoiceText("）。再点一次「语音」将改用浏览器识别。"), "warn");
      return false;
    }
    try {
      await fetch("/api/asr/prepare", { method: "POST" });
    } catch (_) {
      /* 下面的轮询会看到结果 */
    }
    if (!alive(token)) return false;
    setPhase("preparing");
    say(info.state === "missing"
      ? cbVoiceText("第一次使用要下载本机识别模型（约 460 MB），下载好之前先不录音。可以先打字；点「取消」不影响后台下载。")
      : cbVoiceText("正在加载本机识别模型，几秒钟后开始录音。"), "");
    while (phase === "preparing" && alive(token)) {
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      if (phase !== "preparing" || !alive(token)) return false; // 点了「取消」
      info = await serverStatus();
      if (!alive(token)) return false;
      if (info && info.state === "ready") {
        setPhase("starting");
        return true;
      }
      if (!info || info.state === "failed" || !info.available) {
        serverBroken = true;
        mode = null;
        setPhase("idle");
        say(cbVoiceText("本机识别模型准备失败") + (info && info.load_error ? "（" + info.load_error + "）" : "") +
          cbVoiceText("。再点一次「语音」将改用浏览器识别。"), "warn");
        return false;
      }
    }
    return false;
  }

  function micError(err) {
    const name = err && err.name;
    if (name === "NotAllowedError" || name === "SecurityError") return cbVoiceText("没有麦克风权限：请在地址栏旁允许本页使用麦克风。");
    if (name === "NotFoundError") return cbVoiceText("没有检测到麦克风。");
    return cbVoiceText("麦克风无法启动：") + (name || cbVoiceText("未知原因"));
  }

  /* ---------- 本机识别：录音 → /api/asr ---------- */
  async function startServer(token) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !window.MediaRecorder) {
      say(cbVoiceText("浏览器不让本页录音：请用 http://127.0.0.1 或 https 打开工作台（局域网 http 地址拿不到麦克风）。"), "warn");
      return;
    }
    try {
      const acquired = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!alive(token)) { acquired.getTracks().forEach((t) => t.stop()); return; }
      stream = acquired;
    } catch (err) {
      if (!alive(token)) return;
      say(micError(err), "warn");
      return;
    }
    const type = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4"]
      .find((t) => MediaRecorder.isTypeSupported(t)) || "";
    chunks = [];
    recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
    recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    recorder.onstop = () => { if (alive(token)) sendServer(token); }; // 麦克风自己停下也处理
    recorder.start();
    setPhase("recording");
    say(cbVoiceText("正在录音，说完再点一次「停止」。最长 ") + MAX_SECONDS + cbVoiceText(" 秒。"), "");
  }

  async function sendServer(token) {
    const blob = new Blob(chunks, { type: (recorder && recorder.mimeType) || "audio/webm" });
    releaseMic();
    chunks = [];
    recorder = null;
    if (!blob.size) {
      setPhase("idle");
      say(cbVoiceText("没有录到声音，请再试一次。"), "warn");
      return;
    }
    setPhase("transcribing");
    say(cbVoiceText("本机识别中…"), "");
    const abort = new AbortController();
    const id = "voice-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
    activeRequest = { abort, id };
    const timer = setTimeout(() => abort.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch("/api/asr", {
        method: "POST", headers: { "Content-Type": blob.type, "X-Civil-ASR-ID": id, "X-Civil-ASR-Language": window.CBI18n?.locale === "en" ? "en" : "zh" }, body: blob, signal: abort.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!alive(token)) return;
      if (response.status >= 500) {
        // 本机识别运行出错：本页不再撞它，下一次改走浏览器识别
        serverBroken = true;
        mode = null;
        throw new Error((data.detail || cbVoiceText("本机识别不可用")) +
          (BrowserRecognition ? cbVoiceText("。再点一次「语音」将改用浏览器识别。") : ""));
      }
      if (!response.ok) throw new Error(data.detail || "HTTP " + response.status);
      if (fill(data.text)) {
        say(cbVoiceText("已转成文字（本机识别，") + data.elapsed_seconds + cbVoiceText(" 秒）。请核对后再发送，不会自动发送。"), "ok");
      }
    } catch (err) {
      if (!alive(token)) return;
      const why = err && err.name === "AbortError" ? cbVoiceText("识别超时") : (err && err.message) || cbVoiceText("未知原因");
      say(cbVoiceText("识别失败：") + why, "warn");
    } finally {
      clearTimeout(timer);
      if (alive(token)) { activeRequest = null; setPhase("idle"); }
    }
  }

  /* ---------- 浏览器自带识别 ---------- */
  function browserConsent() {
    let remembered = null;
    try { remembered = localStorage.getItem(CONSENT_KEY); } catch (_) { /* 隐私模式 */ }
    if (remembered === "yes") return true;
    const ok = window.confirm(
      cbVoiceText("本机语音识别不可用，将改用浏览器自带识别。\n") +
      cbVoiceText("浏览器会把录音发送到浏览器厂商的服务器处理（Chrome 为 Google）。\n") +
      cbVoiceText("涉密或内网项目请不要使用。是否继续？")
    );
    if (ok) {
      try { localStorage.setItem(CONSENT_KEY, "yes"); } catch (_) { /* 隐私模式 */ }
    }
    return ok;
  }

  function startBrowser(token) {
    if (!browserConsent()) {
      say(cbVoiceText("已取消。本机识别需要在 Python 工作台安装 requirements-asr.txt。"), "");
      return;
    }
    recognition = new BrowserRecognition();
    recognition.lang = window.CBI18n?.locale === "en" ? "en-US" : "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    let finalText = "";
    let failed = false;
    recognition.onresult = (event) => {
      if (!alive(token)) return;
      let partial = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const piece = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += piece;
        else partial += piece;
      }
      // 实时文字不进读屏播报区：边听边念会被麦克风再收进去
      interim(finalText + partial);
    };
    recognition.onerror = (event) => {
      if (!alive(token)) return;
      failed = true;
      const why = event.error === "not-allowed" ? cbVoiceText("没有麦克风权限") :
        event.error === "network" ? cbVoiceText("浏览器识别服务连不上（断网或所在网络无法访问）") :
        event.error === "no-speech" ? cbVoiceText("没有听到说话") : event.error;
      say(cbVoiceText("浏览器识别失败：") + why, "warn");
    };
    recognition.onend = () => {
      if (!alive(token)) return;
      recognition = null;
      interim("");
      setPhase("idle");
      if (finalText.trim()) {
        if (fill(finalText)) say(cbVoiceText("已转成文字（浏览器识别）。请核对后再发送，不会自动发送。"), "ok");
      } else if (!failed) {
        say(cbVoiceText("没有听清，请再说一次。"), "warn");
      }
    };
    try {
      recognition.start();
    } catch (err) {
      recognition = null;
      say(cbVoiceText("浏览器识别无法启动：") + (err && err.message ? err.message : cbVoiceText("未知原因")), "warn");
      return;
    }
    setPhase("recording");
    say(cbVoiceText("正在听（浏览器识别），说完再点一次「停止」。最长 ") + MAX_SECONDS + cbVoiceText(" 秒。"), "");
  }

  /* ---------- 共用 ---------- */
  function releaseMic() {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
  }

  function stopRecording() {
    if (recorder && recorder.state !== "inactive") recorder.stop();
    else if (recognition) recognition.stop();
  }

  btn.addEventListener("click", async () => {
    if (phase === "recording") {
      stopRecording();
      return;
    }
    if (phase === "preparing") {
      cancel(cbVoiceText("已取消等待，模型在后台继续准备。准备好后再点「语音」。"));
      return;
    }
    if (phase === "transcribing") {
      cancel();
      return;
    }
    if (phase !== "idle") return;
    const token = ++generation;
    setPhase("starting"); // 同步占位：第一次 await 之前就挡住第二次点击，不会开出两个录音
    try {
      const current = await detectMode();
      if (!alive(token)) return;
      if (current === "server") {
        if (await ensureReady(token)) await startServer(token);
        return;
      }
      if (current === "browser") return startBrowser(token);
      btn.title = cbVoiceText("当前浏览器不支持语音识别，本机识别也未安装");
      say(cbVoiceText("语音输入不可用：本机未安装识别引擎，浏览器也不支持语音识别。"), "warn");
    } finally {
      if (alive(token) && phase === "starting") setPhase("idle");
    }
  });

  window.addEventListener("cb:languagechange", () => { cancel(cbVoiceText("语言已切换，旧语音草稿已取消。")); paint(); });
  paint();
})();
