<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const LEVELS = [
  { value: 'full_trust', label: '全自动', desc: '免审批直接执行' },
  { value: 'auto_with_notification', label: '自动+通知', desc: '自动执行，界面提示' },
  { value: 'ask_first', label: '逐次审批', desc: '每次调用前询问' },
  { value: 'approve_always', label: '记住批准', desc: '批准一次后同类放行' },
  { value: 'manual_only', label: '仅人工', desc: '转人工执行' },
]

const tools = ref([])
const loading = ref(true)
const saving = ref(null)   // 正在保存的工具名
const error = ref('')

const levelLabel = (v) => LEVELS.find((l) => l.value === v)?.label || v

async function reload() {
  loading.value = true
  try {
    tools.value = await api.listTools()
    error.value = ''
  } catch (e) {
    error.value = String(e.message || e)
  }
  loading.value = false
}

async function changePermission(tool, event) {
  const permission = event.target.value
  saving.value = tool.name
  try {
    await api.setToolPermission(tool.name, permission)
    await reload()
  } catch (e) {
    error.value = String(e.message || e)
    await reload()
  }
  saving.value = null
}

async function clearOverride(tool) {
  saving.value = tool.name
  try {
    await api.clearToolPermission(tool.name)
    await reload()
  } catch (e) {
    error.value = String(e.message || e)
  }
  saving.value = null
}

onMounted(reload)
defineExpose({ reload })
</script>

<template>
  <div class="list">
    <div v-if="error" class="err-bar">{{ error }}</div>
    <div v-if="loading" class="empty">加载中…</div>
    <template v-else>
      <div v-for="t in tools" :key="t.name" class="tool" :class="{ overridden: t.override }">
        <div class="tool-head">
          <span class="tool-name">{{ t.name }}</span>
          <span v-if="t.path_guard" class="tool-guard" title="路径参数受目录沙箱保护">路径护栏</span>
        </div>
        <div class="tool-desc" :title="t.description">{{ t.description }}</div>
        <div class="tool-row">
          <select
            :value="t.override || t.default"
            :disabled="saving === t.name"
            @change="changePermission(t, $event)"
          >
            <option v-for="l in LEVELS" :key="l.value" :value="l.value">{{ l.label }}</option>
          </select>
          <span class="eff" :class="{ changed: t.override }">
            {{ t.override ? `生效: ${levelLabel(t.effective)}（覆盖默认 ${levelLabel(t.default)}）` : `生效: ${levelLabel(t.effective)}` }}
          </span>
          <button
            v-if="t.override"
            class="reset"
            :disabled="saving === t.name"
            title="清除覆盖，回到声明默认"
            @click="clearOverride(t)"
          >↺</button>
        </div>
      </div>
      <div v-if="!tools.length" class="empty">无已注册工具</div>
      <div class="note">修改立即生效并持久化（SQLite tool_permissions 表），对当前与后续所有会话生效。</div>
    </template>
  </div>
</template>

<style scoped>
.list { flex: 1; overflow-y: auto; padding: 6px; }
.err-bar { margin: 4px 6px; padding: 6px 10px; background: #fbeeee; color: var(--bad); font-size: 12px; border-radius: 3px; word-break: break-all; }
.tool { padding: 9px 10px; border-bottom: 1px solid var(--line); }
.tool.overridden { background: #fdf8ef; }
.tool-head { display: flex; align-items: center; gap: 6px; }
.tool-name { font-family: var(--mono); font-size: 12.5px; font-weight: 600; }
.tool-guard { font-size: 10px; color: var(--ok); border: 1px solid var(--ok); border-radius: 2px; padding: 0 4px; }
.tool-desc { font-size: 11.5px; color: var(--ink-3); margin-top: 3px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tool-row { display: flex; align-items: center; gap: 6px; margin-top: 6px; }
.tool-row select {
  border: 1px solid var(--line); border-radius: 3px; padding: 3px 4px; font-size: 12px;
  background: #fff; color: var(--ink); cursor: pointer; outline: none; flex-shrink: 0;
}
.tool-row select:focus { border-color: var(--accent); }
.tool-row select:disabled { opacity: 0.5; }
.eff { font-size: 10.5px; color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.eff.changed { color: var(--warn); }
.reset { border: 1px solid var(--line); background: #fff; color: var(--ink-2); border-radius: 3px; cursor: pointer; font-size: 12px; padding: 1px 6px; flex-shrink: 0; }
.reset:hover { border-color: var(--accent); color: var(--accent); }
.note { padding: 10px; font-size: 10.5px; color: var(--ink-3); line-height: 1.6; }
.empty { padding: 14px 10px; font-size: 12px; color: var(--ink-3); text-align: center; }
</style>
