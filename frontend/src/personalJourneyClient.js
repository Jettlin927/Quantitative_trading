export class PersonalJourneyError extends Error {
  constructor(message, { code = 'personal_request_failed', status = 0 } = {}) {
    super(message)
    this.name = 'PersonalJourneyError'
    this.code = code
    this.status = status
  }
}

export class PersonalJourneyClient {
  constructor({ fetcher = globalThis.fetch } = {}) {
    this.fetcher = fetcher === globalThis.fetch ? fetcher.bind(globalThis) : fetcher
  }

  /** @param {{ signal?: AbortSignal }} [options] */
  openToday({ signal } = {}) {
    return this.#request('/api/personal/today', { signal })
  }

  /** @param {{ question: string, idempotencyKey: string, signal?: AbortSignal }} options */
  createSyntheticTrace({ question, idempotencyKey, signal }) {
    return this.#command('/api/personal/synthetic-traces', { question }, { idempotencyKey, signal })
  }

  /** @param {{ analysisId: string, previewSha256: string, idempotencyKey: string, signal?: AbortSignal }} options */
  saveSyntheticRecord({ analysisId, previewSha256, idempotencyKey, signal }) {
    return this.#command('/api/personal/synthetic-records', {
      analysis_id: analysisId,
      preview_sha256: previewSha256,
    }, { idempotencyKey, signal })
  }

  /** @param {string} analysisId @param {{ signal?: AbortSignal }} [options] */
  observeAnalysisEvents(analysisId, { signal } = {}) {
    return this.#request(`/api/personal/analyses/${encodeURIComponent(analysisId)}/events`, {
      headers: { Accept: 'text/event-stream' },
      signal,
      responseType: 'response',
    })
  }

  /** @param {string} path @param {unknown} body @param {{ idempotencyKey: string, signal?: AbortSignal }} options */
  #command(path, body, { idempotencyKey, signal }) {
    return this.#request(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
        'X-Personal-Request': '1',
      },
      body: JSON.stringify(body),
      signal,
    })
  }

  async #request(path, options = {}) {
    if (!path.startsWith('/api/personal/')) throw new PersonalJourneyError('只允许访问同源个人工作台端点。')
    const { responseType, ...fetchOptions } = options
    const response = await this.fetcher(path, { credentials: 'same-origin', cache: 'no-store', ...fetchOptions })
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      const detail = payload?.detail
      throw new PersonalJourneyError(
        typeof detail === 'object' ? detail?.message : detail || `${path} 返回 ${response.status}`,
        { code: detail?.code, status: response.status },
      )
    }
    return responseType === 'response' ? response : response.json()
  }
}

export const browserPersonalJourneyClient = new PersonalJourneyClient()
