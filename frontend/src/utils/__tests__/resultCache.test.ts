/**
 * Unit tests for frontend/src/utils/resultCache.ts
 *
 * Vitest runs under the "node" environment (see vite.config.ts), so
 * localStorage does not exist by default.  We provide a minimal
 * in-memory stub and install it as a global with vi.stubGlobal before
 * any test runs.
 *
 * NOTE on test 7 (savedAt structural guard) — PRODUCTION-CODE BLOCKER:
 *   When savedAt is a non-numeric value, `Date.now() - savedAt` evaluates to
 *   NaN, and `NaN > ttlMs` is false, so the malformed entry is returned
 *   instead of null.  Test 7 documents the desired guard behaviour and will
 *   FAIL until resultCache.ts adds an explicit type check:
 *     if (typeof entry.savedAt !== 'number') { return null }
 */

import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'

import {
  clearResultCache,
  loadResultCache,
  saveResultCache,
} from '../resultCache'

// ---------------------------------------------------------------------------
// Minimal localStorage stub (node-safe)
// ---------------------------------------------------------------------------

const _store: Record<string, string> = {}

const localStorageMock = {
  getItem(key: string): string | null {
    return Object.prototype.hasOwnProperty.call(_store, key) ? _store[key] : null
  },
  setItem(key: string, value: string): void {
    _store[key] = value
  },
  removeItem(key: string): void {
    delete _store[key]
  },
  clear(): void {
    Object.keys(_store).forEach((k) => delete _store[k])
  },
}

beforeAll(() => {
  vi.stubGlobal('localStorage', localStorageMock)
})

afterAll(() => {
  vi.unstubAllGlobals()
})

beforeEach(() => {
  localStorageMock.clear()
  vi.restoreAllMocks()
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

// ---------------------------------------------------------------------------

describe('loadResultCache', () => {
  it('returns null when key is absent', () => {
    // Arrange — store is empty (cleared in beforeEach)

    // Act
    const result = loadResultCache('csp')

    // Assert
    expect(result).toBeNull()
  })

  it('returns null and removes key after TTL expires', () => {
    // Arrange
    vi.setSystemTime(new Date(1_000_000))
    saveResultCache('csp', [{ ticker: 'AAPL' }])

    // Act — advance 1 ms past the 30-minute TTL
    vi.advanceTimersByTime(30 * 60 * 1000 + 1)
    const result = loadResultCache('csp')

    // Assert
    expect(result).toBeNull()
    expect(localStorageMock.getItem('screener:csp')).toBeNull()
  })

  it('returns null if JSON is malformed', () => {
    // Arrange
    localStorageMock.setItem('screener:csp', '{ not valid json }')

    // Act
    const result = loadResultCache('csp')

    // Assert
    expect(result).toBeNull()
  })

  // -------------------------------------------------------------------------
  // PRODUCTION-CODE BLOCKER — see module docstring.
  // This test will FAIL until a typeof guard is added in resultCache.ts.
  // -------------------------------------------------------------------------
  it('returns null if savedAt is not a number', () => {
    // Arrange — inject a structurally malformed cache entry
    localStorageMock.setItem(
      'screener:csp',
      JSON.stringify({ data: { ticker: 'AAPL' }, savedAt: 'not-a-number' }),
    )

    // Act
    const result = loadResultCache('csp')

    // Assert
    expect(result).toBeNull()
  })
})

describe('saveResultCache', () => {
  it('persists data; loadResultCache returns data and savedAt', () => {
    // Arrange
    vi.setSystemTime(new Date(1_000_000))
    const payload = [{ ticker: 'AAPL', score: 75 }]

    // Act
    saveResultCache('csp', payload)
    const result = loadResultCache<typeof payload>('csp')

    // Assert
    expect(result).not.toBeNull()
    expect(result!.data).toEqual(payload)
    expect(result!.savedAt).toBe(1_000_000)
  })

  it('silently handles localStorage quota errors', () => {
    // Arrange — simulate a full storage quota
    vi.spyOn(localStorageMock, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError')
    })

    // Act — must not propagate the exception
    expect(() => saveResultCache('csp', [{ ticker: 'AAPL' }])).not.toThrow()
  })
})

describe('clearResultCache', () => {
  it('removes the key from localStorage', () => {
    // Arrange
    saveResultCache('csp', [{ ticker: 'AAPL' }])

    // Act
    clearResultCache('csp')

    // Assert
    expect(localStorageMock.getItem('screener:csp')).toBeNull()
  })
})
