<script setup>
import { ref, reactive, onMounted } from 'vue'
import { api } from '../api.js'

// 技能面板：展示已安装技能（含生效状态与来源），支持上传 zip / URL 安装 / 自定义创建 / 启停 / 卸载
const skills = ref([])
const loading = ref(true)
const busy = ref('')
const error = ref('')
const notice = ref('')

const expanded = ref('')      // 展开详情的 slug
const body = ref('')
const fileInput = ref(null)
const panel = ref('')         // 当前展开的操作区：upload / url / create
const urlInput = ref('')      // URL 安装的直链

const form = reactive({
  slug: '',
  name: '',
  description: '',
  body: '',
  scriptName: '',
  scriptSource: '',
  credentials: '',
})

function flash(msg) {
  notice.value = msg
  setTimeout(() => { if (notice.value === msg) notice.value = '' }, 4000)
}

async function reload() {
  loading.value = true
  try {
    skills.value = await api.listSkills()
    error.value = ''
  } catch (e) {
    error.value = String(e.message || e)
  }
  loading.value = false
}

async function run(key, fn) {
  busy.value = key
  try {
    await fn()
  } catch (e) {
    error.value = String(e.message || e)
  } finally {
    busy.value = ''
  }
}

const toggle = (s) => run(s.slug, async () => {
  await api.setSkillEnabled(s.slug, !s.enabled)
  await reload()
  flash(`已${s.enabled ? '停用' : '启用'} ${s.slug}`)
})

const reset = (s) => run(s.slug, async () => {
  await api.clearSkillEnabled(s.slug)
  await reload()
  flash(`${s.slug} 已回到配置默认`)
})

const remove = (s) => run(s.slug, async () => {
  if (!window.confirm(`卸载技能「${s.name}」（${s.slug}）？目录会被删除。`)) return
  await api.deleteSkill(s.slug)
  if (expanded.value === s.slug) { expanded.value = ''; body.value = '' }
  await reload()
  flash(`已卸载 ${s.slug}`)
})

async function detail(s) {
  if (expanded.value === s.slug) { expanded.value = ''; body.value = ''; return }
  await run(s.slug, async () => {
    const d = await api.getSkill(s.slug)
    expanded.value = s.slug
    body.value = d.body || '(无正文)'
  })
}

function pickFile() {
  panel.value = panel.value === 'upload' ? '' : 'upload'
}

async function onFile(e) {
  const file = e.target.files?.[0]
  e.target.value = ''
  if (!file) return
  await run('upload', async () => {
    const r = await api.installSkillZip(file)
    panel.value = ''
    await reload()
    flash(`已安装 ${r.slug} v${r.version || '-'}`)
  })
}

async function installUrl() {
  const url = urlInput.value.trim()
  if (!url) return
  await run('url', async () => {
    const r = await api.installSkillUrl(url)
    urlInput.value = ''
    panel.value = ''
    await reload()
    flash(`已安装 ${r.slug} v${r.version || '-'}`)
  })
}

function parseCredentials(text) {
  // 每行一个：ENV_NAME / ENV_NAME 可选（带"可选"表示非必需，缺失不阻塞执行）
  return text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      const optional = line.includes('可选')
      const env = line.replace(/可选/g, '').trim()
      return { name: env.toLowerCase(), env, description: '', required: !optional }
    })
    .filter((c) => c.env)
}

async function submitCreate() {
  const name = form.name.trim()
  const description = form.description.trim()
  if (!name || !description) { error.value = '技能名称与描述必填'; return }
  const payload = {
    name,
    description,
    slug: form.slug.trim(),
    body: form.body,
    credentials: parseCredentials(form.credentials),
    scripts: form.scriptName.trim() && form.scriptSource.trim()
      ? { [form.scriptName.trim()]: form.scriptSource }
      : {},
  }
  await run('create', async () => {
    const r = await api.createSkill(payload)
    panel.value = ''
    Object.assign(form, { slug: '', name: '', description: '', body: '', scriptName: '', scriptSource: '', credentials: '' })
    await reload()
    flash(`已创建 ${r.slug}（默认未启用，勾选开关即可生效）`)
  })
}

onMounted(reload)
defineExpose({ reload })
</script>

<template>
  <div class="list">
    <div v-if="error" class="bar err" @click="error = ''">{{ error }}</div>
    <div v-if="notice" class="bar ok" @click="notice = ''">{{ notice }}</div>

    <div class="actions">
      <button :class="{ on: panel === 'upload' }" @click="pickFile">上传 zip</button>
      <button :class="{ on: panel === 'url' }" :disabled="busy === 'url'"
              @click="panel = panel === 'url' ? '' : 'url'">URL 安装</button>
      <button :class="{ on: panel === 'create' }" @click="panel = panel === 'create' ? '' : 'create'">＋ 新建</button>
    </div>

    <div v-if="panel === 'upload'" class="form">
      <input ref="fileInput" type="file" accept=".zip" :disabled="busy === 'upload'" @change="onFile">
      <p class="hint">选择技能包 zip（含 SKILL.md 与 scripts/），安装后默认不启用。</p>
    </div>

    <div v-if="panel === 'url'" class="form">
      <input v-model="urlInput" placeholder="https://…/skill-1.0.0.zip" @keyup.enter="installUrl">
      <button class="primary" :disabled="!urlInput.trim() || busy === 'url'" @click="installUrl">下载并安装</button>
      <p class="hint">仅支持 http/https 直链，且必须是 zip 技能包。</p>
    </div>

    <div v-if="panel === 'create'" class="form">
      <input v-model="form.name" placeholder="技能名称（必填）">
      <input v-model="form.slug" placeholder="标识 slug（可选，默认按名称生成）">
      <input v-model="form.description" placeholder="一句话描述（必填，模型据此判断相关性）">
      <textarea v-model="form.body" rows="4" placeholder="正文（Markdown：任务目标 / 操作步骤 / 响应结构…）"></textarea>
      <textarea v-model="form.credentials" rows="2" placeholder="凭证（每行一个环境变量名，如 SHOWAPI_APP_KEY；行尾加「可选」表示非必需）"></textarea>
      <input v-model="form.scriptName" placeholder="脚本文件名（可选，如 report.py）">
      <textarea v-model="form.scriptSource" rows="4" placeholder="脚本内容（可选，argparse CLI，stdout 输出 JSON）"></textarea>
      <button class="primary" :disabled="busy === 'create'" @click="submitCreate">创建技能</button>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <template v-else>
      <div v-for="s in skills" :key="s.slug" class="skill" :class="{ on: s.enabled, busy: busy === s.slug }">
        <div class="head">
          <input
            class="switch"
            type="checkbox"
            :checked="s.enabled"
            :disabled="busy === s.slug"
            :title="s.enabled ? '已启用：模型可见并可调用' : '未启用：模型完全看不到'"
            :aria-label="(s.enabled ? '停用技能 ' : '启用技能 ') + s.name"
            @change="toggle(s)"
          >
          <span class="name" :title="s.name">{{ s.name }}</span>
          <span v-if="s.version" class="ver">v{{ s.version }}</span>
          <button v-if="s.enabled_source === 'override'" class="mini" title="清除覆盖，回到配置默认" @click="reset(s)">↺</button>
          <button class="mini danger" title="卸载" @click="remove(s)">✕</button>
        </div>

        <div class="meta">
          <span class="slug">{{ s.slug }}</span>
          <span class="src">{{ s.enabled_source === 'override' ? '界面覆盖' : s.enabled_source === 'config' ? '配置启用' : '未启用' }}</span>
        </div>

        <div class="desc" @click="detail(s)">{{ expanded === s.slug ? '收起详情 ▲' : s.description }}</div>

        <div v-if="s.scripts.length" class="scripts" :title="s.scripts.join(', ')">脚本：{{ s.scripts.join(', ') }}</div>
        <div v-if="s.missing_env.length" class="warn" title="在仓库根 .env 配置后重启进程即可执行相关脚本">
          缺凭证：{{ s.missing_env.join(', ') }}
        </div>
        <div v-if="s.dependencies_declared.length" class="note">声明依赖：{{ s.dependencies_declared.join(', ') }}</div>

        <pre v-if="expanded === s.slug" class="body">{{ body }}</pre>
      </div>
      <div v-if="!skills.length" class="empty">尚未安装技能。可以上传 zip、给直链，或直接新建。</div>
      <div class="note foot">
        勾选即时生效并持久化（SQLite skill_states）；执行技能脚本需人工审批（工具权限可改）。
      </div>
    </template>
  </div>
</template>

<style scoped>
.list { flex: 1; overflow-y: auto; padding: 6px; }
.bar { margin: 4px 6px; padding: 6px 8px; border-radius: 3px; font-size: 11.5px; word-break: break-all; cursor: pointer; }
.bar.err { background: #fbeeee; color: var(--bad); }
.bar.ok { background: #eef7ee; color: var(--ok); }
.actions { display: flex; gap: 6px; padding: 4px 4px 6px; }
.actions button {
  flex: 1; padding: 5px 4px; font-size: 11.5px; cursor: pointer; border-radius: 3px;
  border: 1px solid var(--line); background: #fff; color: var(--ink-2);
}
.actions button.on { border-color: var(--accent); color: var(--accent); background: var(--accent-bg); }
.form { display: flex; flex-direction: column; gap: 5px; padding: 6px 6px 10px; border-bottom: 1px solid var(--line); }
.form input, .form textarea {
  border: 1px solid var(--line); border-radius: 3px; padding: 5px 7px; font-size: 12px;
  font-family: inherit; outline: none; width: 100%; box-sizing: border-box; resize: vertical;
}
.form input:focus, .form textarea:focus { border-color: var(--accent); }
.form .primary { border: 1px solid var(--accent); background: var(--accent); color: #fff; border-radius: 3px; padding: 5px; font-size: 12px; cursor: pointer; }
.form .primary:disabled { opacity: 0.45; cursor: not-allowed; }
.hint { font-size: 10.5px; color: var(--ink-3); line-height: 1.5; margin: 0; }
.skill { padding: 8px 8px 9px; border-bottom: 1px solid var(--line); }
.skill.on { background: #f4f9f4; }
.skill.busy { opacity: 0.55; }
.head { display: flex; align-items: center; gap: 6px; }
/* 开关直接用 checkbox 本体（appearance:none）：DOM 简单、读屏可见、可点击 */
.switch {
  appearance: none; -webkit-appearance: none;
  position: relative; width: 26px; height: 15px; margin: 0; flex-shrink: 0;
  background: #cfd6dc; border: none; border-radius: 8px; cursor: pointer; outline: none;
  transition: background 0.15s;
}
.switch::after {
  content: ''; position: absolute; width: 11px; height: 11px; border-radius: 50%; background: #fff;
  top: 2px; left: 2px; transition: transform 0.15s;
}
.switch:checked { background: var(--ok); }
.switch:checked::after { transform: translateX(11px); }
.switch:focus-visible { box-shadow: 0 0 0 2px var(--accent-bg); }
.switch:disabled { opacity: 0.5; cursor: not-allowed; }
.name { font-size: 12.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.ver { font-family: var(--mono); font-size: 10px; color: var(--ink-3); }
.mini {
  border: 1px solid var(--line); background: #fff; color: var(--ink-2); border-radius: 3px;
  cursor: pointer; font-size: 11px; padding: 0 4px; flex-shrink: 0;
}
.mini:hover { border-color: var(--accent); color: var(--accent); }
.mini.danger:hover { border-color: var(--bad); color: var(--bad); }
.meta { display: flex; gap: 6px; align-items: center; margin: 3px 0 0 32px; }
.slug { font-family: var(--mono); font-size: 10.5px; color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.src { font-size: 10px; color: var(--warn); flex-shrink: 0; }
.desc {
  font-size: 11.5px; color: var(--ink-2); margin-top: 4px; cursor: pointer; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.desc:hover { color: var(--accent); }
.scripts, .note { font-size: 10.5px; color: var(--ink-3); margin-top: 3px; font-family: var(--mono); overflow: hidden; text-overflow: ellipsis; }
.warn { font-size: 10.5px; color: var(--warn); margin-top: 3px; }
.body {
  margin: 6px 0 0; padding: 6px; background: #fff; border: 1px solid var(--line); border-radius: 3px;
  font-size: 10.5px; line-height: 1.5; white-space: pre-wrap; word-break: break-word;
  max-height: 220px; overflow: auto; font-family: var(--mono); color: var(--ink-2);
}
.empty { padding: 14px 10px; font-size: 12px; color: var(--ink-3); text-align: center; }
.foot { padding: 10px; line-height: 1.6; font-family: inherit; }
</style>
