<script setup>
import { ref } from 'vue'

defineProps({ disabled: Boolean })
const emit = defineEmits(['send'])
const draft = ref('')
const inputEl = ref(null)

function doSend() {
  const text = draft.value.trim()
  if (!text) return
  emit('send', text)
  draft.value = ''
  if (inputEl.value) inputEl.value.style.height = 'auto'
}
function autoResize(e) {
  e.target.style.height = 'auto'
  e.target.style.height = Math.min(120, e.target.scrollHeight) + 'px'
}
</script>

<template>
  <div class="input-bar">
    <textarea
      ref="inputEl"
      v-model="draft"
      rows="1"
      placeholder="输入消息…（Enter 发送，Shift+Enter 换行）"
      @keydown.enter.exact.prevent="doSend"
      @input="autoResize"
    ></textarea>
    <button :disabled="disabled || !draft.trim()" @click="doSend">发送</button>
  </div>
</template>

<style scoped>
.input-bar { display: flex; gap: 10px; padding: 14px 22px; border-top: 1px solid var(--line); background: var(--panel); }
.input-bar textarea { flex: 1; resize: none; border: 1px solid var(--line); border-radius: 4px; padding: 10px 12px; font: inherit; max-height: 120px; outline: none; }
.input-bar textarea:focus { border-color: var(--accent); }
.input-bar button { padding: 0 26px; background: var(--accent); color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
.input-bar button:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
