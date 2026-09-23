<script setup>
import { ref, reactive, computed, nextTick, onMounted } from 'vue'
import SessionList from './components/SessionList.vue'
import MemoryPanel from './components/MemoryPanel.vue'
import ToolPanel from './components/ToolPanel.vue'
import StatsPanel from './components/StatsPanel.vue'
import MessageFeed from './components/MessageFeed.vue'
import ApprovalCard from './components/ApprovalCard.vue'
import InputBar from './components/InputBar.vue'
import { api } from './api.js'

// ---------- 状态 ----------
const tab = ref('sessions')
const sessionId = ref(null)
const sessTitle = ref('')
const agentName = ref('Agent')   // 由 /api/health 按当前项目配置填充
const feed = ref([])
const streaming = ref(false)
const statusText = ref('就绪')
const usage = ref(null)
const pendingApproval = ref(null)
const sessionsRef = ref(null)   // SessionList 组件句柄
const feedEl = ref(null)

const canSend = computed(() => !streaming.value)

// ---------- 工具 ----------
async function scrollBottom() {
  await nextTick()
  const el = feedEl.value
  if (el) el.scrollTop = el.scrollHeight
}
function push(item) {
  const r = reactive(item) // 必须包装：直接 mutate push 返回的原始对象不会触发 Vue 更新
  feed.value.push(r)
  scrollBottom()
  return r
}
function setStatus(t) { statusText.value = t }

// ---------- 会话 ----------
async function onSwitchSession(s) {
  if (streaming.value) return
  sessionId.value = s.session_id
  sessTitle.value = s.title
  pendingApproval.value = null
  const msgs = await api.listMessages(s.session_id)
  feed.value = msgs
    .filter((m) => !(m.role === 'assistant' && !m.content && !m.tool_calls))
    .map((m) => {
      if (m.is_summary) return { type: 'summary', text: m.content }
      if (m.role === 'user') return { type: 'user', text: m.content }
      if (m.role === 'assistant' && m.content) return { type: 'assistant', text: m.content }
      return null
    })
    .filter(Boolean)
  scrollBottom()
}
async function onNewSession() {
  const s = await api.createSession()
  sessionId.value = s.session_id
  sessTitle.value = s.title
  feed.value = []
  usage.value = null
  pendingApproval.value = null
  sessionsRef.value?.reload()
}
async function onDeleteSession(id) {
  await api.deleteSession(id)
  if (id === sessionId.value) {
    sessionId.value = null
    feed.value = []
    sessTitle.value = ''
  }
  sessionsRef.value?.reload()
}

// ---------- SSE 消费 ----------
async function runStream(path, body) {
  streaming.value = true
  let cur = null
  try {
    for await (const ev of api.stream(path, body)) {
      const d = ev.data || {}
      switch (ev.type) {
        case 'run_started': setStatus('运行中…'); break
        case 'turn_started': setStatus(`第 ${d.turn} 轮`); break
        case 'delta':
          if (!cur || cur.done) cur = push({ type: 'assistant', text: '', streaming: true })
          cur.text += d.text
          setStatus('生成中…')
          scrollBottom()
          break
        case 'tool_executed':
          if (cur) { cur.streaming = false; cur = null }
          push({ type: 'tool', tool: d.tool, text: d.result || '', err: d.ok === false })
          break
        case 'approval_required':
          if (cur) { cur.streaming = false; cur = null }
          pendingApproval.value = d
          setStatus('等待审批')
          break
        case 'context_compressed':
          push({ type: 'summary', text: `[上下文已压缩] 摘要 ${d.summary_tokens ?? '—'} tokens，覆盖 ${d.summarized_messages ?? '—'} 条旧消息` })
          break
        case 'budget_exceeded':
          setStatus(`⚠ 轮次预算耗尽（${d.max_turns}）`)
          break
        case 'error':
          push({ type: 'tool', tool: 'error', text: d.message || '未知错误', err: true })
          setStatus('出错')
          break
        case 'turn_finished':
          if (cur) { cur.streaming = false; cur = null }
          usage.value = d.usage || usage.value
          break
        case 'run_finished':
          usage.value = d.usage || usage.value
          pendingApproval.value = null // 正常结束才清除；挂起结束（审批等待）必须保留
          setStatus('就绪')
          break
      }
    }
  } catch (e) {
    push({ type: 'tool', tool: 'error', text: String(e.message || e), err: true })
    setStatus('出错')
  } finally {
    streaming.value = false
    sessionsRef.value?.reload()
  }
}

// ---------- 发送 / 审批 ----------
function onSend(text) {
  if (!sessionId.value) {
    onNewSession().then(() => {
      push({ type: 'user', text })
      runStream('/chat', { session_id: sessionId.value, message: text })
    })
    return
  }
  push({ type: 'user', text })
  runStream('/chat', { session_id: sessionId.value, message: text })
}
function onApproval(decision, remember) {
  const d = pendingApproval.value
  if (!d) return
  pendingApproval.value = null
  runStream('/approvals', {
    session_id: sessionId.value,
    call_id: d.call_id,
    decision,
    remember,
  })
}

// ---------- 记忆/工具面板 ----------
function openMemories() { tab.value = 'memories' }

onMounted(async () => {
  setStatus('就绪')
  try {
    const h = await api.health()
    if (h.agent_name) {
      agentName.value = h.agent_name
      document.title = h.agent_name
    }
  } catch {
    // 取不到就用默认名，不影响对话
  }
})
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <h1>{{ agentName }}</h1>
      <p>Vue 3 SFC · Vite</p>
    </div>
    <button class="btn-new" :disabled="streaming" @click="onNewSession">＋ 新会话</button>
    <div class="tabs">
      <button :class="{ on: tab === 'sessions' }" @click="tab = 'sessions'">会话</button>
      <button :class="{ on: tab === 'memories' }" @click="openMemories">记忆</button>
      <button :class="{ on: tab === 'tools' }" @click="tab = 'tools'">工具</button>
    </div>
    <SessionList
      v-show="tab === 'sessions'"
      ref="sessionsRef"
      :active-id="sessionId"
      @switch="onSwitchSession"
      @delete="onDeleteSession"
    />
    <MemoryPanel v-if="tab === 'memories'" />
    <ToolPanel v-if="tab === 'tools'" />
    <StatsPanel :usage="usage" />
  </aside>

  <div class="main">
    <div class="header">
      <span class="title">
        <span class="dot" :class="{ busy: streaming }"></span>
        {{ sessionId ? sessTitle : '选择或新建会话' }}
      </span>
      <span class="status">{{ statusText }}</span>
    </div>

    <div ref="feedEl" class="feed">
      <div v-if="!feed.length" class="empty-main">输入消息开始对话</div>
      <MessageFeed :feed="feed" />
      <ApprovalCard
        v-if="pendingApproval"
        :approval="pendingApproval"
        :disabled="streaming"
        @decide="onApproval"
      />
    </div>

    <InputBar :disabled="!canSend" @send="onSend" />
  </div>
</template>

<style scoped>
.sidebar {
  width: 262px; background: var(--panel); border-right: 1px solid var(--line);
  display: flex; flex-direction: column; flex-shrink: 0;
}
.brand { padding: 16px 16px 12px; border-bottom: 1px solid var(--line); }
.brand h1 { font-size: 16px; font-weight: 600; }
.brand p { font-size: 11px; color: var(--ink-3); margin-top: 2px; font-family: var(--mono); }
.btn-new {
  margin: 12px 12px 8px; padding: 8px; border: 1px solid var(--accent);
  background: var(--accent); color: #fff; cursor: pointer; font-size: 13px; border-radius: 3px;
}
.btn-new:disabled { opacity: 0.5; cursor: not-allowed; }
.tabs { display: flex; margin: 0 12px 6px; border: 1px solid var(--line); border-radius: 3px; overflow: hidden; }
.tabs button { flex: 1; padding: 6px; border: none; background: none; cursor: pointer; font-size: 12.5px; color: var(--ink-2); }
.tabs button.on { background: var(--accent-bg); color: var(--accent); font-weight: 600; }
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.header {
  padding: 12px 22px; border-bottom: 1px solid var(--line); background: var(--panel);
  display: flex; justify-content: space-between; align-items: center;
}
.title { font-weight: 600; font-size: 14px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); display: inline-block; margin-right: 7px; }
.dot.busy { background: var(--warn); animation: pulse 1s infinite; }
@keyframes pulse { 50% { opacity: 0.35; } }
.status { font-family: var(--mono); font-size: 11px; color: var(--ink-3); }
.feed { flex: 1; overflow-y: auto; padding: 22px 26px; display: flex; flex-direction: column; gap: 13px; }
.empty-main { margin: auto; color: var(--ink-3); font-size: 13px; }
@media (max-width: 760px) {
  .sidebar { display: none; }
}
</style>
