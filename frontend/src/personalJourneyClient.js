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

  /** @param {{ signal?: AbortSignal }} [options] */
  openPortfolio({ signal } = {}) {
    return this.#request('/api/personal/portfolio', { signal })
  }

  /** @param {{ limit?: number, signal?: AbortSignal }} [options] */
  openEquityHistory({ limit = 180, signal } = {}) {
    return this.#request(`/api/personal/portfolio/equity-history?limit=${limit}`, { signal })
  }

  /** @param {string} symbol @param {{ signal?: AbortSignal }} [options] */
  openInstrument(symbol, { signal } = {}) {
    return this.#request(`/api/personal/instruments/${encodeURIComponent(symbol)}`, { signal })
  }

  /** @param {{ signal?: AbortSignal }} [options] */
  listRuleTemplates({ signal } = {}) {
    return this.#request('/api/personal/rule-templates', { signal })
  }

  /** @param {{ signal?: AbortSignal }} [options] */
  openRules({ signal } = {}) {
    return this.#request('/api/personal/rules', { signal })
  }

  /** @param {{ command: unknown, idempotencyKey: string, signal?: AbortSignal }} options */
  submitRuleCommand({ command, idempotencyKey, signal }) {
    return this.#command('/api/personal/rules/commands', command, { idempotencyKey, signal })
  }

  /** @param {{ command: unknown, idempotencyKey: string, signal?: AbortSignal }} options */
  submitPortfolioCommand({ command, idempotencyKey, signal }) {
    return this.#command('/api/personal/portfolio/commands', command, { idempotencyKey, signal })
  }

  /** @param {{ question: string, subjectIds: string[], selectedPrivateFields?: string[], idempotencyKey: string, signal?: AbortSignal }} options */
  prepareAnalysis({ question, subjectIds, selectedPrivateFields = [], idempotencyKey, signal }) {
    return this.#command('/api/personal/analysis-drafts', {
      question,
      subject_ids: subjectIds,
      selected_private_fields: selectedPrivateFields,
    }, { idempotencyKey, signal })
  }

  /** @param {string} draftId @param {{ signal?: AbortSignal }} [options] */
  openAnalysisDraft(draftId, { signal } = {}) {
    return this.#request(`/api/personal/analysis-drafts/${encodeURIComponent(draftId)}`, { signal })
  }

  /** @param {{ draftId: string, previewSha256: string, idempotencyKey: string, signal?: AbortSignal }} options */
  startAnalysis({ draftId, previewSha256, idempotencyKey, signal }) {
    return this.#command('/api/personal/analyses', {
      draft_id: draftId,
      preview_sha256: previewSha256,
    }, { idempotencyKey, signal })
  }

  /** @param {string} runId @param {{ signal?: AbortSignal }} [options] */
  openAnalysis(runId, { signal } = {}) {
    return this.#request(`/api/personal/analyses/${encodeURIComponent(runId)}`, { signal })
  }

  /** @param {{ signal?: AbortSignal }} [options] */
  listAnalysisCapabilities({ signal } = {}) {
    return this.#request('/api/personal/analysis-capabilities', { signal })
  }

  /** @param {{ signal?: AbortSignal }} [options] */
  listAnalyses({ signal } = {}) {
    return this.#request('/api/personal/analyses?limit=20', { signal })
  }

  /** @param {{ runId: string, idempotencyKey: string, signal?: AbortSignal }} options */
  cancelAnalysis({ runId, idempotencyKey, signal }) {
    return this.#command(`/api/personal/analyses/${encodeURIComponent(runId)}/cancel`, {}, { idempotencyKey, signal })
  }

  /** @param {{ question: string, idempotencyKey: string, signal?: AbortSignal }} options */
  createSyntheticTrace({ question, idempotencyKey, signal }) {
    return this.#command('/api/personal/synthetic-traces', { question }, { idempotencyKey, signal })
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
