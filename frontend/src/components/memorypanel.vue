<script setup>
import { ref } from 'vue'
import { api } from '../api.js'

const memories = ref([])
const newMemory = ref('')
const loading = ref(true)

async function reload() {
  memories.value = await api.listMemories()
  loading.value = false
}
async function add() {
  const content = newMemory.value.trim()
  if (!content) return
  await api.createMemory(content, 'manual')
  newMemory.value = ''
  reload()
}
async function remove(id) {
  await api.deleteMemory(id)
  reload()
}
reload()
</script>

<template>
  <div class="list">
    <div class="mem-add">
      <input v-model="newMemory" placeholder="写入一条记忆…" @keyup.enter="add">
      <button :disabled="!newMemory.trim()" @click="add">存</button>
    </div>
    <div v-if="loading" class="empty">加载中…</div>
    <template v-else>
      <div v-for="m in memories" :key="m.memory_id" class="mem">
        <span class="mem-kind">{{ m.kind }}</span>
        <span class="mem-content" :title="m.content">{{ m.content }}</span>
        <button class="del" title="删除" @click="remove(m.memory_id)">✕</button>
      </div>
      <div v-if="!memories.length" class="empty">暂无长期记忆</div>
    </template>
  </div>
</template>

<style scoped>
.list { flex: 1; overflow-y: auto; padding: 4px 6px; }
.mem-add { display: flex; gap: 6px; padding: 6px 4px; }
.mem-add input { flex: 1; min-width: 0; border: 1px solid var(--line); border-radius: 3px; padding: 6px 8px; font-size: 12.5px; outline: none; }
.mem-add input:focus { border-color: var(--accent); }
.mem-add button { border: 1px solid var(--accent); background: var(--accent); color: #fff; border-radius: 3px; padding: 0 10px; cursor: pointer; font-size: 12.5px; }
.mem-add button:disabled { opacity: 0.4; cursor: not-allowed; }
.mem { display: flex; gap: 6px; align-items: center; padding: 7px 8px; border-bottom: 1px solid var(--line); font-size: 12.5px; }
.mem-kind { font-family: var(--mono); font-size: 10px; color: var(--accent); background: var(--accent-bg); padding: 0 4px; border-radius: 2px; flex-shrink: 0; }
.mem-content { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink-2); }
.del { border: none; background: none; color: var(--ink-3); cursor: pointer; font-size: 12px; visibility: hidden; flex-shrink: 0; }
.mem:hover .del { visibility: visible; }
.del:hover { color: var(--bad); }
.empty { padding: 14px 10px; font-size: 12px; color: var(--ink-3); text-align: center; }
</style>
