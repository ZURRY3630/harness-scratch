<script setup>
import { ref } from 'vue'

defineProps({ approval: Object, disabled: Boolean })
const emit = defineEmits(['decide'])
const remember = ref(false)
</script>

<template>
  <div class="approval">
    <h3>⚠ 工具调用等待审批</h3>
    <div class="kv">工具：<code>{{ approval.tool_name }}</code></div>
    <div class="kv">参数：<code>{{ JSON.stringify(approval.arguments) }}</code></div>
    <label><input v-model="remember" type="checkbox"> 记住同类调用（APPROVE_ALWAYS）</label>
    <div class="hint">提示：不批准而直接发送新消息，将自动取消此调用</div>
    <div class="row">
      <button class="btn-approve" :disabled="disabled" @click="emit('decide', 'approved', remember)">批准</button>
      <button class="btn-deny" :disabled="disabled" @click="emit('decide', 'denied', remember)">拒绝</button>
    </div>
  </div>
</template>

<style scoped>
.approval { align-self: center; border: 1px solid var(--warn); background: #fdf8ef; padding: 14px 18px; border-radius: 4px; max-width: 88%; }
.approval h3 { font-size: 13.5px; color: var(--warn); margin-bottom: 8px; }
.approval .kv { margin-top: 4px; font-size: 13px; }
.approval code { font-family: var(--mono); font-size: 12px; background: #f6efe2; padding: 1px 5px; border-radius: 2px; word-break: break-all; }
.approval label { display: flex; gap: 6px; align-items: center; font-size: 12px; color: var(--ink-2); margin-top: 9px; cursor: pointer; }
.approval .hint { font-size: 11px; color: var(--ink-3); margin-top: 5px; }
.approval .row { display: flex; gap: 10px; margin-top: 11px; }
.approval button { flex: 1; padding: 7px; border-radius: 3px; cursor: pointer; font-size: 13px; border: 1px solid var(--line); }
.approval button:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-approve { background: var(--ok); color: #fff; border-color: var(--ok) !important; }
.btn-deny { background: #fff; color: var(--bad); border-color: var(--bad) !important; }
</style>
