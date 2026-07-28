const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

/**
 * ReadAdapter interface: callers provide only a path and optional AbortSignal.
 * It performs one JSON read, has no cache/retry/polling, and rejects with a
 * Chinese-readable HTTP detail when the response is not successful.
 */
export async function browserReadAdapter({ path, signal = undefined }) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    signal,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(body?.detail || `${path} 返回 ${response.status}`)
  }
  return response.json()
}

export const systemClock = {
  now: () => new Date(),
}
