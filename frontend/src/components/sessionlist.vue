<script setup>
defineProps({ activeId: String })
defineEmits(['switch', 'delete'])
import { ref, onMounted } from 'vue'
import { api } from '../api.js'

const sessions = ref([])

async function reload() {
  sessions.value = await api.listSessions()
}
defineExpose({ reload })
onMounted(reload)
</script>

<template>
  <div class="list">
    <div
      v-for="s in sessions"
      :key="s.session_id"
      class="sess"
      :class="{ active: s.session_id === activeId }"
      @click="$emit('switch', s)"
    >
      <span class="sess-title">{{ s.title }}</span>
      <button class="del" title="删除" @click.stop="$emit('delete', s.session_id)">✕</button>
    </div>
    <div v-if="!sessions.length" class="empty">暂无会话</div>
  </div>
</template>

<style scoped>
.list { flex: 1; overflow-y: auto; padding: 4px 6px; }
.sess {
  padding: 8px 10px; border-radius: 3px; cursor: pointer; font-size: 13px;
  color: var(--ink-2); display: flex; justify-content: space-between; gap: 6px; align-items: center;
}
.sess:hover { background: var(--bg); }
.sess.active { background: var(--accent-bg); color: var(--accent); font-weight: 600; }
.sess-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.del { border: none; background: none; color: var(--ink-3); cursor: pointer; font-size: 12px; visibility: hidden; flex-shrink: 0; }
.sess:hover .del { visibility: visible; }
.del:hover { color: var(--bad); }
.empty { padding: 14px 10px; font-size: 12px; color: var(--ink-3); text-align: center; }
</style>
