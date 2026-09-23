/* MiniHarness · Vue 3 组合式 API */
const { createApp, ref, computed, nextTick, onMounted } = Vue;

createApp({
  setup() {
    // ---------- 状态 ----------
    const tab = ref("sessions");
    const sessions = ref([]);
    const memories = ref([]);
    const newMemory = ref("");
    const sessionId = ref(null);
    const sessTitle = ref("");
    const feed = ref([]);
    const draft = ref("");
    const streaming = ref(false);
    const statusText = ref("就绪");
    const usage = ref(null);
    const pendingApproval = ref(null);
    const remember = ref(false);
    const feedEl = ref(null);
    const inputEl = ref(null);

    const approvalArgs = computed(() =>
      pendingApproval.value ? JSON.stringify(pendingApproval.value.arguments) : ""
    );

    // ---------- 基础 ----------
    const scrollBottom = async () => {
      await nextTick();
      const el = feedEl.value;
      if (el) el.scrollTop = el.scrollHeight;
    };

    const push = (item) => { feed.value.push(item); scrollBottom(); return item; };

    const api = async (path, opts) => {
      const r = await fetch("/api" + path, opts);
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      return r.json();
    };
    const postJSON = (path, body) =>
      api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

    // ---------- 会话 ----------
    const loadSessions = async () => { sessions.value = await api("/sessions"); };

    const loadMessages = async () => {
      const msgs = await api(`/sessions/${sessionId.value}/messages`);
      feed.value = msgs
        .filter((m) => !(m.role === "assistant" && !m.content && !m.tool_calls))
        .map((m) => {
          if (m.is_summary) return { type: "summary", text: m.content };
          if (m.role === "user") return { type: "user", text: m.content };
          if (m.role === "assistant" && m.content) return { type: "assistant", text: m.content };
          return null;
        })
        .filter(Boolean);
      scrollBottom();
    };

    const switchSession = async (s) => {
      if (streaming.value) return;
      sessionId.value = s.session_id;
      sessTitle.value = s.title;
      pendingApproval.value = null;
      await loadMessages();
      loadSessions();
    };

    const newSession = async () => {
      const s = await postJSON("/sessions", {});
      sessionId.value = s.session_id;
      sessTitle.value = s.title;
      feed.value = [];
      usage.value = null;
      pendingApproval.value = null;
      loadSessions();
    };

    const deleteSession = async (id) => {
      await api(`/sessions/${id}`, { method: "DELETE" });
      if (id === sessionId.value) { sessionId.value = null; feed.value = []; sessTitle.value = ""; }
      loadSessions();
    };

    // ---------- 长期记忆 ----------
    const openMemories = async () => {
      tab.value = "memories";
      memories.value = await api("/memories");
    };
    const addMemory = async () => {
      const content = newMemory.value.trim();
      if (!content) return;
      await postJSON("/memories", { content, kind: "manual" });
      newMemory.value = "";
      memories.value = await api("/memories");
    };
    const deleteMemory = async (id) => {
      await api(`/memories/${id}`, { method: "DELETE" });
      memories.value = await api("/memories");
    };

    // ---------- SSE 消费 ----------
    const consumeSSE = async (resp) => {
      const reader = resp.body.getReader();
      const dec = new TextDecoder();
      let buf = "", cur = null;

      const handle = (ev) => {
        const d = ev.data || {};
        switch (ev.type) {
          case "run_started": statusText.value = "运行中…"; break;
          case "turn_started": statusText.value = `第 ${d.turn} 轮`; break;
          case "delta":
            if (!cur || cur.done) cur = push({ type: "assistant", text: "", streaming: true });
            cur.text += d.text;
            statusText.value = "生成中…";
            scrollBottom();
            break;
          case "tool_executed":
            if (cur) { cur.streaming = false; cur = null; }
            push({ type: "tool", tool: d.tool, text: d.result || "", err: d.ok === false });
            break;
          case "approval_required":
            if (cur) { cur.streaming = false; cur = null; }
            pendingApproval.value = d;
            statusText.value = "等待审批";
            break;
          case "context_compressed":
            push({ type: "summary", text: `[上下文已压缩] 摘要 ${d.summary_tokens ?? "—"} tokens，覆盖 ${d.summarized_messages ?? "—"} 条旧消息` });
            break;
          case "budget_exceeded":
            statusText.value = `⚠ 轮次预算耗尽（${d.max_turns}）`;
            break;
          case "error":
            push({ type: "tool", tool: "error", text: d.message || "未知错误", err: true });
            statusText.value = "出错";
            break;
          case "turn_finished":
            if (cur) { cur.streaming = false; cur = null; }
            usage.value = d.usage || usage.value;
            break;
          case "run_finished":
            usage.value = d.usage || usage.value;
            statusText.value = "就绪";
            break;
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          if (raw === "data: [DONE]") continue;
          if (!raw.startsWith("data: ")) continue;
          try { handle(JSON.parse(raw.slice(6))); } catch { /* 忽略坏帧 */ }
        }
      }
      if (cur) cur.streaming = false;
    };

    const startStream = async (path, body) => {
      streaming.value = true;
      try {
        const r = await fetch("/api" + path, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error(`请求失败 ${r.status}`);
        await consumeSSE(r);
      } catch (e) {
        push({ type: "tool", tool: "error", text: String(e.message || e), err: true });
        statusText.value = "出错";
      } finally {
        streaming.value = false;
        pendingApproval.value = null;
        loadSessions();
      }
    };

    // ---------- 发送 / 审批 ----------
    const send = () => {
      const text = draft.value.trim();
      if (!text || streaming.value) return;
      if (!sessionId.value) { newSession().then(() => doSend(text)); return; }
      doSend(text);
    };
    const doSend = (text) => {
      push({ type: "user", text });
      draft.value = "";
      if (inputEl.value) inputEl.value.style.height = "auto";
      startStream("/chat", { session_id: sessionId.value, message: text });
    };

    const answerApproval = (decision) => {
      const d = pendingApproval.value;
      if (!d) return;
      pendingApproval.value = null;
      startStream("/approvals", {
        session_id: sessionId.value,
        call_id: d.call_id,
        decision,
        remember: remember.value,
      });
    };

    // ---------- 输入框 ----------
    const autoResize = (e) => {
      e.target.style.height = "auto";
      e.target.style.height = Math.min(120, e.target.scrollHeight) + "px";
    };

    onMounted(loadSessions);
    return {
      tab, sessions, memories, newMemory, sessionId, sessTitle, feed, draft,
      streaming, statusText, usage, pendingApproval, remember, feedEl, inputEl,
      approvalArgs, newSession, switchSession, deleteSession, openMemories,
      addMemory, deleteMemory, send, answerApproval, autoResize,
    };
  },
}).mount("#app");
