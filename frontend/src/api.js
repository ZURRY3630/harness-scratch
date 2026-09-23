/* 后端 API 封装：REST + SSE 消费器 */

async function jsonOrThrow(r) {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}

export const api = {
  // ---- 运行时信息（当前项目的 agent 名等）----
  health: () => fetch('/api/health').then(jsonOrThrow),

  // ---- 会话 ----
  listSessions: () => fetch('/api/sessions').then(jsonOrThrow),
  createSession: () => fetch('/api/sessions', { method: 'POST' }).then(jsonOrThrow),
  deleteSession: (id) => fetch(`/api/sessions/${id}`, { method: 'DELETE' }).then(jsonOrThrow),
  listMessages: (sid) => fetch(`/api/sessions/${sid}/messages`).then(jsonOrThrow),

  // ---- 长期记忆 ----
  listMemories: () => fetch('/api/memories').then(jsonOrThrow),
  createMemory: (content, kind = 'manual') =>
    fetch('/api/memories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, kind }),
    }).then(jsonOrThrow),
  deleteMemory: (id) => fetch(`/api/memories/${id}`, { method: 'DELETE' }).then(jsonOrThrow),

  // ---- 工具管理 ----
  listTools: () => fetch('/api/tools').then(jsonOrThrow),
  setToolPermission: (name, permission) =>
    fetch(`/api/tools/${encodeURIComponent(name)}/permission`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ permission }),
    }).then(jsonOrThrow),
  clearToolPermission: (name) =>
    fetch(`/api/tools/${encodeURIComponent(name)}/permission`, { method: 'DELETE' }).then(jsonOrThrow),

  // ---- SSE 流（聊天 / 审批续跑）。onEvent 返回 true 表示流正常结束 ----
  async *stream(path, body) {
    const resp = await fetch('/api' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!resp.ok) throw new Error(`请求失败 ${resp.status}`)
    const reader = resp.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        if (raw === 'data: [DONE]') return
        if (!raw.startsWith('data: ')) continue
        try {
          yield JSON.parse(raw.slice(6))
        } catch {
          /* 跳过坏帧 */
        }
      }
    }
  },
}
