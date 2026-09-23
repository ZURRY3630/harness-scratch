<script setup>
defineProps({ feed: Array })
</script>

<template>
  <template v-for="(m, i) in feed" :key="i">
    <div v-if="m.type === 'user'" class="msg user">{{ m.text }}</div>

    <div v-else-if="m.type === 'assistant'" class="msg assistant">
      {{ m.text }}<span v-if="m.streaming" class="caret"></span>
    </div>

    <div v-else-if="m.type === 'summary'" class="msg summary">{{ m.text }}</div>

    <div v-else-if="m.type === 'tool'" class="tool-card" :class="{ err: m.err }">
      <div class="tt">{{ m.err ? '✕' : '⚙' }} {{ m.tool }}</div>
      <div class="tr">{{ m.text }}</div>
    </div>
  </template>
</template>

<style scoped>
.msg { max-width: 78%; padding: 10px 14px; border-radius: 6px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.msg.user { align-self: flex-end; background: var(--accent); color: #fff; }
.msg.assistant { align-self: flex-start; background: var(--panel); border: 1px solid var(--line); }
.msg.summary { align-self: center; background: var(--accent-bg); border: 1px dashed var(--accent); color: var(--accent); font-size: 12.5px; max-width: 90%; }
.caret { display: inline-block; width: 7px; height: 14px; background: var(--accent); vertical-align: text-bottom; margin-left: 2px; animation: pulse 0.8s infinite; }
@keyframes pulse { 50% { opacity: 0.35; } }
.tool-card {
  align-self: flex-start; background: var(--panel); border: 1px solid var(--line);
  border-left: 3px solid var(--accent); padding: 9px 14px; border-radius: 4px;
  max-width: 82%; font-family: var(--mono); font-size: 12.5px;
}
.tool-card.err { border-left-color: var(--bad); }
.tool-card .tt { font-weight: 600; margin-bottom: 3px; }
.tool-card .tr { color: var(--ink-2); word-break: break-all; }
</style>
