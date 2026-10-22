const jsonHeaders = { 'Content-Type': 'application/json' }

async function parse(response) {
  let body = null
  try {
    body = await response.json()
  } catch {
    body = null
  }
  if (!response.ok) {
    const detail = (body && body.detail) || `请求失败（${response.status}）`
    const error = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
    error.status = response.status
    throw error
  }
  return body
}

export function createApi(baseUrl) {
  const base = baseUrl.replace(/\/+$/, '')

  return {
    base,
    async config() {
      return parse(await fetch(`${base}/api/config`))
    },
    async createTransfer(files) {
      return parse(
        await fetch(`${base}/api/transfers`, {
          method: 'POST',
          headers: jsonHeaders,
          body: JSON.stringify({ files }),
        }),
      )
    },
    async join(code) {
      return parse(
        await fetch(`${base}/api/transfers/join`, {
          method: 'POST',
          headers: jsonHeaders,
          body: JSON.stringify({ code }),
        }),
      )
    },
    async getTransfer(token, key) {
      return parse(await fetch(`${base}/api/transfers/${token}?key=${encodeURIComponent(key)}`))
    },
    async uploadChunk(token, key, idx, offset, blob) {
      const url = `${base}/api/transfers/${token}/chunk?key=${encodeURIComponent(key)}&idx=${idx}&offset=${offset}`
      return parse(await fetch(url, { method: 'POST', body: blob }))
    },
    async switchRelay(token, key, reason) {
      return parse(
        await fetch(`${base}/api/transfers/${token}/switch-relay?key=${encodeURIComponent(key)}`, {
          method: 'POST',
          headers: jsonHeaders,
          body: JSON.stringify({ reason }),
        }),
      )
    },
    async reportProgress(token, key, files) {
      return parse(
        await fetch(`${base}/api/transfers/${token}/progress?key=${encodeURIComponent(key)}`, {
          method: 'POST',
          headers: jsonHeaders,
          body: JSON.stringify({ files }),
        }),
      )
    },
    async submitHashes(token, key, files) {
      return parse(
        await fetch(`${base}/api/transfers/${token}/verify?key=${encodeURIComponent(key)}`, {
          method: 'POST',
          headers: jsonHeaders,
          body: JSON.stringify({ files }),
        }),
      )
    },
    async cancel(token, key) {
      return parse(
        await fetch(`${base}/api/transfers/${token}?key=${encodeURIComponent(key)}`, { method: 'DELETE' }),
      )
    },
    downloadUrl(token, key, idx) {
      return `${base}/d/${token}/${idx}?key=${encodeURIComponent(key)}`
    },
  }
}

export function openSignaling(baseUrl, token, role, key) {
  const url = new URL(baseUrl, window.location.href)
  const scheme = url.protocol === 'https:' ? 'wss:' : 'ws:'
  const path = `${url.pathname.replace(/\/+$/, '')}/ws/${token}?role=${role}&key=${encodeURIComponent(key)}`
  return new WebSocket(`${scheme}//${url.host}${path}`)
}
