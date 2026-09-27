const cbStreamText = (source, values = {}) => globalThis.CBI18n?.t(source, values) ?? String(source || "").replace(/\{(\w+)\}/g, (match, key) => values[key] ?? match);
/* One turn of the conversation, from the page's side.
 *
 * The first /api/chat stream, a resume after a dropped connection (GET
 * /api/sessions/{sid}/events?after=<last id>) and a full replay when the user comes back to a
 * running session all feed the same handler; repeated ids are skipped. Only the user's stop
 * (AbortError) and a server-reported error (TurnError) end a turn early — a network error is
 * a drop, retried with backoff until done or the server says the turn is over.
 *
 * Everything the page must do for a turn is handed in as deps (see createTurnStream), so this
 * file reads nothing global:
 *   state            { session, history, summoned, attachments, attachmentRoles, cadProjectId }
 *   run              { active(), setActive(r), paint(bool), releaseWatch(), watch(sid, opts), background: Set }  (bound as `runs`)
 *   ui               { log(), addMsg(role, who, text), addStatus(text), announce(text), doc }
 *   hitl             { confirmed(), typed(), clear(), enable(data), pending(data) }
 *   turnUi           { tlCreate, routePaint, collaborationPaint, obStep, paintContext, estimateLocalContext,
 *                      renderCites, appendDocCards, fixMount, classifyMissing, refreshAuditSoon, skillWho,
 *                      namesOrPlain, setLastDeliverables }
 *   projectId(), loadThreads(), apiError(res), capability(name), stream.read, fetch, AbortController
 */
export function createTurnStream(deps) {
  const { state, run: runs, ui, hitl, turnUi } = deps; // `run` is a turn object inside the functions below

  async function streamChat(message, bodyEl, run) {
    const confirmed = hitl.confirmed();
    const typed = hitl.typed();
    hitl.clear(); // Consume this turn's typed response; never reuse a server gate.
    const res = await deps.fetch("/api/chat", {
      method: "POST",
      signal: run.controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        locale: globalThis.CBI18n?.locale || "zh-CN",
        // The server owns full history; this bounded fallback excludes this turn.
        history: state.history.slice(-81, -1),
        expert_ids: [...state.summoned],
        confirm_ok: confirmed, // Compatibility only; both hosts validate this turn's exact confirm_text.
        confirm_text: typed,
        session_id: state.session,
        project_id: deps.projectId() || "",
        cad_project_id: state.cadProjectId || "",
        planning_project_id: state.planningProjectId || "",
        logistics_project_id: state.logisticsProjectId || "",
        attachments: state.attachments
          .filter((a) => !String(a.id || "").startsWith("job:"))
          .map((a) => a.id),
        attachment_roles: Object.fromEntries(state.attachments.filter(a => !String(a.id || "").startsWith("job:") &&
          ["tender", "response", "reference"].includes(state.attachmentRoles[a.id])).map(a => [a.id, state.attachmentRoles[a.id]])),
      }),
    });
    if (!res.ok) {
      throw new Error(await deps.apiError(res));
    }
    /* ux(round3)：本条消息挂一条阶段时间线（完成后折叠为一行摘要）；ux(round5)：消息原文供审批卡「确认并重提」 */
    const v = turnView(message, bodyEl, run);
    run.view = v;
    try {
      try {
        await deps.stream.read(res.body, turnHandler(v), { signal: run.controller.signal });
      } catch (e) {
        /* 真实浏览器里连接被掐是 reader.read() 直接 reject（TypeError），不是流悄悄结束：
           同样按断流处理；用户停止（AbortError）和服务端明确报错（TurnError）原样上抛。 */
        if (!e || e.name === "AbortError" || e.name === "TurnError") throw e;
      }
      if (!v.complete) {
        /* 流断了（锁屏 / 切 App / 换网络）：服务端这一轮还在跑，并且每一帧都有编号——
           从最后看到的编号往后续，跟到 done。没有这个能力的后端仍走轮询恢复。 */
        if (deps.capability("event_log") === true) await resumeTurn(v);
        if (!v.complete) {
          const dropped = new Error("回答连接已中断，正在从服务端恢复结果…");
          dropped.name = "StreamDroppedError";
          throw dropped;
        }
      }
      deps.loadThreads().catch(() => {});
    } catch (err) {
      if (runs.active() === run && v.tl) v.tl.error(err.name === "AbortError" ? cbStreamText("已停止接收回答") : String(err.message || err));
      throw err;
    }
  }

  /* 一轮回答在页面上的状态：首次的 /api/chat 流、断线后的 /events 续流、切回来时的全量回放，
     三条路都喂同一个处理器。bodyEl 可以先空着（切回来的会话），第一帧正文到了再补气泡。 */
  function turnView(message, bodyEl, run) {
    return { message, bodyEl, run, tl: bodyEl ? turnUi.tlCreate(bodyEl, message) : null,
      acc: "", complete: false, recorded: false, lastSeq: Number(run.lastSeq) || 0 };
  }

  function turnBubble(v, who) {
    if (v.bodyEl) return v.bodyEl;
    v.bodyEl = ui.addMsg("assistant", who || turnUi.namesOrPlain(), "");
    v.run.bodyEl = v.bodyEl;
    v.tl = turnUi.tlCreate(v.bodyEl, v.message);
    return v.bodyEl;
  }

  /* 服务端说的错（error 事件、格式错）和网络断掉不是一回事：前者不重连。 */
  function turnError(text) {
    const e = new Error(text);
    e.name = "TurnError";
    return e;
  }

  function turnHandler(v) {
    const run = v.run;
    return (eventName, dataLine, id) => {
      if (runs.active() !== run || state.session !== run.session) return;
      const seq = Number(id);
      if (seq > 0) {
        if (seq <= v.lastSeq) return; // 续流时的重复帧
        v.lastSeq = seq;
        run.lastSeq = seq;
      }
      if (!["context", "status", "token", "error", "done", "collaboration"].includes(eventName)) return;
      let data;
      try { data = JSON.parse(dataLine); }
      catch (_) { throw turnError("服务器返回了无法解析的回答事件，请重试。"); }
      if (!data || typeof data !== "object" || Array.isArray(data)) {
        throw turnError("服务器返回的回答事件格式不完整，请重试。");
      }
      if (eventName === "context") {
        turnUi.paintContext(data);
      }
      if (eventName === "status") {
        hitl.enable(data);
        if (data.phase === "routing" && data.route) turnUi.routePaint(data.route, turnBubble(v), v.message);
        if (v.tl) v.tl.status(data);
        if (data.phase === "summon") {
          v.complete = false;
          if (v.acc) {
            if (!v.recorded) state.history.push({ role: "assistant", content: v.acc });
            v.acc = "";
            v.bodyEl = ui.addMsg("assistant", turnUi.skillWho(data.expert || "", "given"), "");
            run.bodyEl = v.bodyEl;
          }
          v.recorded = false;
          const bodyEl = turnBubble(v, turnUi.skillWho(data.expert || "", "given"));
          const who = bodyEl.parentElement && bodyEl.parentElement.querySelector(".who");
          if (who && data.expert) who.textContent = turnUi.skillWho(data.expert, data.skill_source || "");
        }
      }
      if (eventName === "collaboration") turnUi.collaborationPaint(data, turnBubble(v));
      if (eventName === "token") {
        v.complete = false;
        v.acc += data.text || "";
        turnBubble(v).textContent = v.acc;
        ui.log().scrollTop = ui.log().scrollHeight;
      }
      if (eventName === "error") {
        /* 已经写好的文书随错误一起来：当场画出来，别让人重开会话才找到 */
        const files = Array.isArray(data.deliverables) ? data.deliverables : [];
        if (files.length) {
          const bodyEl = turnBubble(v);
          turnUi.setLastDeliverables(files);
          turnUi.appendDocCards(files, bodyEl, { runs: data.deliverable_runs });
        }
        if (data.partial_text && !v.acc) {
          v.acc = data.partial_text;
          turnBubble(v).textContent = v.acc;
        }
        throw turnError(data.text || "error");
      }
      if (eventName === "done") {
        const bodyEl = turnBubble(v);
        if (hitl.pending(data)) hitl.enable(data);
        else hitl.clear();
        if (data.route) turnUi.routePaint(data.route, bodyEl, v.message);
        if (data.collaboration) turnUi.collaborationPaint(data.collaboration, bodyEl);
        v.complete = true;
        if (v.tl) {
          if (hitl.pending(data)) v.tl.finish(data);
          else if (data.ok === false) v.tl.error(data.text || "工具未完成本轮任务");
          else v.tl.finish(data);
        }
        if (data.ok !== false && !hitl.pending(data)) turnUi.obStep(2);
        /* ux(round11)：流式收口才播报一行（只抄事件字段，不刷屏，附录 J） */
        ui.announce(data.cancelled ? "任务已停止，已有结果已保留。" : hitl.pending(data) ? "等待签认：请在审批卡键入完整签认句后确认。" : data.ok === false ? "本轮未完成：请查看时间线和工具结果。" : "回答完毕" + (Array.isArray(data.deliverables) && data.deliverables.length ? " · 文书 " + data.deliverables.length + " 份" : ""));
        v.acc = data.text || v.acc;
        if (typeof turnUi.markdown === "function") turnUi.markdown(bodyEl, v.acc); else bodyEl.textContent = v.acc;
        const whoEl = bodyEl.parentElement && bodyEl.parentElement.querySelector(".who");
        if (whoEl) whoEl.textContent = turnUi.skillWho(data.skill || data.expert || "", data.skill_source || "");
        if (v.acc && !v.recorded) {
          state.history.push({ role: "assistant", content: v.acc });
          v.recorded = true;
        }
        if (data.context) turnUi.paintContext(data.context);
        else turnUi.paintContext(turnUi.estimateLocalContext());
        turnUi.renderCites(data.citations || [], bodyEl);
        turnUi.setLastDeliverables(Array.isArray(data.deliverables) ? data.deliverables : []); /* ux(round9)：/doc 最近交付物 */
        turnUi.appendDocCards(data.deliverables || [], bodyEl, { runs: data.deliverable_runs });
        /* ux(round7)：缺数引导条——UNSPECIFIED/[A001] 徽章旁的「去补数」，预填草稿不自动发送 */
        const miss = turnUi.classifyMissing(v.acc);
        if (miss) turnUi.fixMount(bodyEl.parentElement, miss);
        turnUi.refreshAuditSoon(); /* ux(round6)：本轮完成 → 审计时间线增量刷新（含决策置顶） */
      }
    };
  }

  /* 续流：GET /api/sessions/{sid}/events?after=N。连不上就退避重试（1 s 起，封顶 15 s），
     直到 done / error 到手、用户点停止（AbortError 原样抛出）、或服务端说这一轮已经不在跑。 */
  async function resumeTurn(v) {
    const run = v.run;
    const sid = run.session;
    let wait = 1000;
    let announced = false;
    while (!v.complete) {
      if (run.controller.signal.aborted) { const e = new Error("aborted"); e.name = "AbortError"; throw e; }
      if (runs.active() !== run || state.session !== sid) return;
      let res = null;
      try {
        res = await deps.fetch(`/api/sessions/${encodeURIComponent(sid)}/events?after=${v.lastSeq}`, { signal: run.controller.signal });
      } catch (e) {
        if (e && e.name === "AbortError") throw e;
      }
      if (res && res.ok) {
        if (announced) { ui.announce("已重新连上，继续接收回答"); announced = false; }
        try {
          await deps.stream.read(res.body, turnHandler(v), { signal: run.controller.signal });
        } catch (e) {
          if (e && (e.name === "AbortError" || e.name === "TurnError")) throw e; // 用户停止 / 服务端明确报错：不重试
        }
        if (v.complete) return;
        wait = 1000;
        /* 流正常结束却没有 done：问一下这一轮还在不在跑；不在了就没什么可等的 */
        let detail = null;
        try { const r = await deps.fetch(`/api/sessions/${encodeURIComponent(sid)}`, { signal: run.controller.signal }); if (r.ok) detail = await r.json(); }
        catch (e) { if (e && e.name === "AbortError") throw e; }
        if (detail && detail.turn_state && !detail.turn_state.active) return;
      } else if (res && (res.status === 404 || res.status === 400)) {
        return; // 没有事件记录：交给轮询恢复
      }
      if (!announced) { ui.announce("连接中断，正在重连…"); announced = true; }
      await new Promise((resolve) => setTimeout(resolve, wait));
      wait = Math.min(wait * 2, 15000);
    }
  }

  /* 切回一个还在跑的会话 / 锁屏回来：没有气泡、没有 run，从头回放这一轮再跟到底。
     和发送时一样占住 runs.active()，停止按钮因此照常工作。 */
  async function attachToTurn(sid, reason) {
    if (deps.capability("event_log") !== true) { runs.watch(sid, { bodyEl: null, reason: reason || "" }); return; }
    if (runs.active() || state.session !== sid) return;
    runs.releaseWatch();
    const run = { controller: new deps.AbortController(), session: sid, bodyEl: null, lastSeq: 0, attached: true };
    const v = turnView("", null, run);
    run.view = v;
    runs.setActive(run);
    runs.paint(true);
    try {
      await resumeTurn(v);
      if (!v.complete && runs.active() === run) {
        runs.setActive(null);
        runs.paint(false);
        runs.watch(sid, { bodyEl: v.bodyEl, reason: reason || "" });
        return;
      }
      if (runs.active() === run) { runs.background.delete(sid); ui.addStatus((reason || "") + "任务已在后台完成，结果已恢复。"); deps.loadThreads().catch(() => {}); }
    } catch (err) {
      if (runs.active() !== run) return;
      const stopped = err && err.name === "AbortError";
      if (v.bodyEl) {
        const note = ui.doc.createElement("p");
        note.className = stopped ? "status-line" : "status-line err";
        note.textContent = stopped ? cbStreamText("已停止接收回答。已有内容已保留。") : String(err.message || err);
        v.bodyEl.parentElement.appendChild(note);
      } else if (!stopped) ui.addStatus(String(err.message || err));
    } finally {
      if (runs.active() === run) {
        runs.setActive(null);
        runs.paint(false);
      }
    }
  }

  return { streamChat, turnView, turnHandler, turnError, resumeTurn, attachToTurn };
}
