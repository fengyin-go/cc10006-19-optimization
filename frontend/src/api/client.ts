/** 统一请求封装：拼后端地址、带上操作人身份、抛可读错误。 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export type Identity = { operator: string; role: string }

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

let identityProvider: () => Identity = () => ({ operator: '', role: '' })

/** 由会话 store 注入当前操作人，保证所有请求都带上 X-Operator。 */
export function setIdentityProvider(provider: () => Identity) {
  identityProvider = provider
}

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  const identity = identityProvider()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(init?.headers as Record<string, string> | undefined),
  }
  if (identity.operator) headers['X-Operator'] = identity.operator
  if (identity.role) headers['X-Role'] = identity.role
  return fetch(url, { ...init, headers }).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '请求未送达'
    throw new Error(`接口请求失败：${detail}`)
  })
}

async function readError(response: Response, fallback: string): Promise<ApiError> {
  let detail = ''
  try {
    const payload = await response.json()
    detail = typeof payload?.detail === 'string' ? payload.detail : ''
  } catch {
    detail = ''
  }
  // 429 等未预期状态也给出状态码，避免把越权提示吞成通用失败。
  return new ApiError(response.status, detail || `${fallback}（HTTP ${response.status}）`)
}

export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw await readError(response, '数据读取失败')
  }
  return (await response.json()) as T
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) {
    throw await readError(response, '操作未生效')
  }
  return (await response.json()) as T
}
