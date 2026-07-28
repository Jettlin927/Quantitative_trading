import { useCallback, useEffect, useRef, useState } from 'react'

const PAGE_SIZE = 50

const emptyStockPage = { items: [], total: 0, limit: PAGE_SIZE, offset: 0 }
const emptyUsPage = { items: [], total: 0, limit: PAGE_SIZE, offset: 0 }

export const READ_OUTCOME = Object.freeze({
  APPLIED: 'applied',
  CANCELLED: 'cancelled',
  STALE: 'stale',
  SKIPPED: 'skipped',
  FAILED: 'failed',
})

const applied = { status: READ_OUTCOME.APPLIED }
const skipped = { status: READ_OUTCOME.SKIPPED }

export function isReadFailure(result) {
  return result?.status === READ_OUTCOME.FAILED
}

export function isReadComplete(result) {
  return result?.status === READ_OUTCOME.APPLIED || result?.status === READ_OUTCOME.SKIPPED
}

/**
 * 股票研究 module interface. It owns A/美股 list-selection-detail consistency,
 * cancellation and stale-response rules. Callers know only user actions and the
 * current view model; endpoint and generation knowledge stays inside.
 */
export function useStockResearch(readAdapter) {
  const [stockPage, setStockPage] = useState(emptyStockPage)
  const [stockQuery, setStockQuery] = useState('')
  const [selectedStockCode, setSelectedStockCode] = useState('')
  const [stockBars, setStockBars] = useState([])
  const [stockDetail, setStockDetail] = useState(null)
  const [stockDetailLoading, setStockDetailLoading] = useState(false)
  const [stockListError, setStockListError] = useState('')
  const [stockDetailError, setStockDetailError] = useState('')

  const [usPage, setUsPage] = useState(emptyUsPage)
  const [usQuery, setUsQuery] = useState('')
  const [selectedUsCode, setSelectedUsCode] = useState('')
  const [loadedUsCode, setLoadedUsCode] = useState('')
  const [usBars, setUsBars] = useState([])
  const [usMarketBars, setUsMarketBars] = useState([])
  const [usDetailLoading, setUsDetailLoading] = useState(false)
  const [usListError, setUsListError] = useState('')
  const [usDetailError, setUsDetailError] = useState('')

  const mounted = useRef(true)
  const aSelection = useRef('')
  const usSelection = useRef('')
  const appliedAQuery = useRef('')
  const appliedUsQuery = useRef('')
  const generations = useRef({ aList: 0, aDetail: 0, usList: 0, usDetail: 0 })
  const controllers = useRef({ aList: null, aDetail: null, usList: null, usDetail: null })
  const pendingDetails = useRef({ aDetail: null, usDetail: null })
  const requestedDetails = useRef({
    aDetail: { selection: '', generation: 0 },
    usDetail: { selection: '', generation: 0 },
  })

  const begin = useCallback((kind) => {
    controllers.current[kind]?.abort()
    const controller = new AbortController()
    controllers.current[kind] = controller
    generations.current[kind] += 1
    return { generation: generations.current[kind], controller }
  }, [])

  const isCurrent = useCallback((kind, request, selection) => mounted.current
    && generations.current[kind] === request.generation
    && (selection === undefined || (kind === 'aDetail' ? aSelection.current : usSelection.current) === selection), [])

  const selectStock = useCallback((code) => {
    if (code === aSelection.current) return
    aSelection.current = code
    generations.current.aDetail += 1
    controllers.current.aDetail?.abort()
    setSelectedStockCode(code)
    setStockBars([])
    setStockDetail(null)
    setStockDetailError('')
    if (!code) setStockDetailLoading(false)
  }, [])

  const selectUsInstrument = useCallback((code) => {
    if (code === usSelection.current) return
    usSelection.current = code
    generations.current.usDetail += 1
    controllers.current.usDetail?.abort()
    setSelectedUsCode(code)
    setLoadedUsCode('')
    setUsBars([])
    setUsMarketBars([])
    setUsDetailError('')
    if (!code) setUsDetailLoading(false)
  }, [])

  const applyStockPage = useCallback((page) => {
    setStockPage(page)
    const current = aSelection.current
    selectStock(page.items.some((item) => item.ts_code === current) ? current : page.items[0]?.ts_code || '')
  }, [selectStock])

  const applyUsPage = useCallback((page) => {
    setUsPage(page)
    const current = usSelection.current
    selectUsInstrument(page.items.some((item) => item.sourceCode === current) ? current : page.items[0]?.sourceCode || '')
  }, [selectUsInstrument])

  const loadStocks = useCallback(async (offset = 0, requestedQuery = appliedAQuery.current, applyQuery = false) => {
    const request = begin('aList')
    setStockListError('')
    try {
      const page = await readAdapter({ path: stockScreenPath(requestedQuery, offset), signal: request.controller.signal })
      if (!isCurrent('aList', request)) return { status: READ_OUTCOME.STALE }
      if (applyQuery) appliedAQuery.current = requestedQuery
      applyStockPage(page)
      return applied
    } catch (error) {
      if (request.controller.signal.aborted) return { status: READ_OUTCOME.CANCELLED }
      if (!isCurrent('aList', request)) return { status: READ_OUTCOME.STALE }
      const message = errorMessage(error)
      setStockListError(message)
      return { status: READ_OUTCOME.FAILED, error: message }
    }
  }, [applyStockPage, begin, isCurrent, readAdapter])

  const loadUsInstruments = useCallback(async (offset = 0, requestedQuery = appliedUsQuery.current, applyQuery = false) => {
    const request = begin('usList')
    setUsListError('')
    try {
      const page = await readAdapter({ path: usInstrumentPath(requestedQuery, offset), signal: request.controller.signal })
      if (!isCurrent('usList', request)) return { status: READ_OUTCOME.STALE }
      if (applyQuery) appliedUsQuery.current = requestedQuery
      applyUsPage(page)
      return applied
    } catch (error) {
      if (request.controller.signal.aborted) return { status: READ_OUTCOME.CANCELLED }
      if (!isCurrent('usList', request)) return { status: READ_OUTCOME.STALE }
      const message = errorMessage(error)
      setUsListError(message)
      return { status: READ_OUTCOME.FAILED, error: message }
    }
  }, [applyUsPage, begin, isCurrent, readAdapter])

  const loadSelectedStock = useCallback((code, force = false) => {
    if (!code) return Promise.resolve(skipped)
    const pending = pendingDetails.current.aDetail
    if (pending?.selection === code) return pending.promise
    const requested = requestedDetails.current.aDetail
    if (!force && requested.selection === code && requested.generation === generations.current.aDetail) {
      return Promise.resolve(skipped)
    }

    const request = begin('aDetail')
    requestedDetails.current.aDetail = { selection: code, generation: request.generation }
    const entry = { selection: code, promise: null }
    setStockDetailLoading(true)
    setStockBars([])
    setStockDetail(null)
    entry.promise = (async () => {
      try {
        const [bars, detail] = await Promise.all([
          readAdapter({ path: `/api/daily-bars?ts_code=${encodeURIComponent(code)}`, signal: request.controller.signal }),
          readAdapter({ path: `/api/stocks/${encodeURIComponent(code)}/detail`, signal: request.controller.signal }),
        ])
        if (!isCurrent('aDetail', request, code)) return { status: READ_OUTCOME.STALE }
        setStockBars(toAShareMarketBars(bars))
        setStockDetail(detail)
        setStockDetailError('')
        return applied
      } catch (error) {
        if (request.controller.signal.aborted) return { status: READ_OUTCOME.CANCELLED }
        if (!isCurrent('aDetail', request, code)) return { status: READ_OUTCOME.STALE }
        const message = errorMessage(error)
        setStockDetailError(message)
        return { status: READ_OUTCOME.FAILED, error: message }
      } finally {
        if (pendingDetails.current.aDetail === entry) pendingDetails.current.aDetail = null
        if (isCurrent('aDetail', request, code)) setStockDetailLoading(false)
      }
    })()
    pendingDetails.current.aDetail = entry
    return entry.promise
  }, [begin, isCurrent, readAdapter])

  const loadSelectedUs = useCallback((code, force = false) => {
    if (!code) return Promise.resolve(skipped)
    const pending = pendingDetails.current.usDetail
    if (pending?.selection === code) return pending.promise
    const requested = requestedDetails.current.usDetail
    if (!force && requested.selection === code && requested.generation === generations.current.usDetail) {
      return Promise.resolve(skipped)
    }

    const request = begin('usDetail')
    requestedDetails.current.usDetail = { selection: code, generation: request.generation }
    const entry = { selection: code, promise: null }
    setUsDetailLoading(true)
    setLoadedUsCode('')
    setUsBars([])
    setUsMarketBars([])
    entry.promise = (async () => {
      try {
        const response = await readAdapter({ path: `/api/us-experiment/instruments/${encodeURIComponent(code)}/daily-bars`, signal: request.controller.signal })
        if (!isCurrent('usDetail', request, code)) return { status: READ_OUTCOME.STALE }
        const bars = response.bars || []
        setUsBars(bars)
        setUsMarketBars(toUsMarketBars(bars))
        setLoadedUsCode(code)
        setUsDetailError('')
        return applied
      } catch (error) {
        if (request.controller.signal.aborted) return { status: READ_OUTCOME.CANCELLED }
        if (!isCurrent('usDetail', request, code)) return { status: READ_OUTCOME.STALE }
        const message = errorMessage(error)
        setUsDetailError(message)
        return { status: READ_OUTCOME.FAILED, error: message }
      } finally {
        if (pendingDetails.current.usDetail === entry) pendingDetails.current.usDetail = null
        if (isCurrent('usDetail', request, code)) setUsDetailLoading(false)
      }
    })()
    pendingDetails.current.usDetail = entry
    return entry.promise
  }, [begin, isCurrent, readAdapter])

  useEffect(() => {
    if (!selectedStockCode) return undefined
    const timer = window.setTimeout(() => loadSelectedStock(selectedStockCode), 0)
    return () => window.clearTimeout(timer)
  }, [loadSelectedStock, selectedStockCode])

  useEffect(() => {
    if (!selectedUsCode) return undefined
    const timer = window.setTimeout(() => loadSelectedUs(selectedUsCode), 0)
    return () => window.clearTimeout(timer)
  }, [loadSelectedUs, selectedUsCode])

  useEffect(() => {
    const activeControllers = controllers.current
    const activeGenerations = generations.current
    mounted.current = true
    return () => {
      mounted.current = false
      Object.values(activeControllers).forEach((controller) => controller?.abort())
      Object.keys(activeGenerations).forEach((kind) => { activeGenerations[kind] += 1 })
      pendingDetails.current = { aDetail: null, usDetail: null }
    }
  }, [])

  const selectedStock = stockPage.items.find((item) => item.ts_code === selectedStockCode) || stockPage.items[0] || null
  const selectedUsInstrument = usPage.items.find((item) => item.sourceCode === selectedUsCode) || usPage.items[0] || null

  return {
    aShare: {
      page: stockPage,
      query: stockQuery,
      setQuery: setStockQuery,
      submitSearch: () => loadStocks(0, stockQuery.trim(), true),
      loadPage: loadStocks,
      select: selectStock,
      selectedCode: selectedStockCode,
      selected: selectedStock,
      bars: stockBars,
      detail: stockDetail,
      loading: stockDetailLoading,
      error: [stockListError, stockDetailError].filter(Boolean).join('；'),
      refreshList: () => loadStocks(0),
      refreshSelected: () => loadSelectedStock(aSelection.current, true),
    },
    us: {
      page: usPage,
      query: usQuery,
      setQuery: setUsQuery,
      submitSearch: () => loadUsInstruments(0, usQuery.trim(), true),
      loadPage: loadUsInstruments,
      select: selectUsInstrument,
      selectedCode: selectedUsCode,
      selected: selectedUsInstrument,
      bars: usBars,
      marketBars: usMarketBars,
      loading: usDetailLoading,
      ready: Boolean(selectedUsCode) && loadedUsCode === selectedUsCode,
      error: [usListError, usDetailError].filter(Boolean).join('；'),
      refreshList: () => loadUsInstruments(0),
      refreshSelected: () => loadSelectedUs(usSelection.current, true),
    },
  }
}

export function toAShareMarketBars(rows) {
  return rows.map((row) => ({
    time: row.trade_date,
    open: row.open,
    high: row.high,
    low: row.low,
    close: row.close,
    volume: row.vol ?? null,
    amount: row.amount ?? null,
    changePercent: row.pct_chg ?? null,
    provenance: { kind: 'actual_market', label: '实际市场数据', source: 'PostgreSQL', sample: false },
  })).sort((left, right) => String(left.time).localeCompare(String(right.time)))
}

export function toUsMarketBars(rows) {
  return rows.map((row) => ({
    time: row.tradeDate,
    open: row.open,
    high: row.high,
    low: row.low,
    close: row.close,
    volume: row.volume ?? null,
    amount: null,
    provenance: {
      kind: 'experimental',
      label: '实验数据',
      source: row.source || 'yfinance',
      researchEligible: false,
      sample: false,
    },
  })).sort((left, right) => String(left.time).localeCompare(String(right.time)))
}

function stockScreenPath(query, offset) {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(Math.max(0, offset)) })
  if (query.trim()) params.set('q', query.trim())
  return `/api/stocks/screen?${params.toString()}`
}

function usInstrumentPath(query, offset) {
  const params = new URLSearchParams({ current_only: 'true', limit: String(PAGE_SIZE), offset: String(Math.max(0, offset)) })
  if (query.trim()) params.set('q', query.trim())
  return `/api/us-experiment/instruments?${params.toString()}`
}

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}
