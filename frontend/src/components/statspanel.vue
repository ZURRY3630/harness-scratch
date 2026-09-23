<script setup>
defineProps({ usage: Object })
</script>

<template>
  <div v-if="usage" class="stats">
    <div><span>上下文估算</span><b>{{ usage.estimated_context_tokens }}</b></div>
    <div><span>prompt / completion</span><b>{{ usage.prompt_tokens }} / {{ usage.completion_tokens }}</b></div>
    <div><span>LLM 调用 / 缓存命中</span><b>{{ usage.llm_calls }} / {{ usage.cache_hits }}</b></div>
    <div><span>压缩次数</span><b>{{ usage.compressions }}</b></div>
  </div>
</template>

<style scoped>
.stats { border-top: 1px solid var(--line); padding: 10px 14px; font-size: 11px; line-height: 2; }
.stats div { display: flex; justify-content: space-between; }
.stats span { color: var(--ink-3); }
.stats b { font-family: var(--mono); font-weight: 600; color: var(--ink); }
</style>
