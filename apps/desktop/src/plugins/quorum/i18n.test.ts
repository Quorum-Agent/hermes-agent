import { describe, expect, it } from 'vitest'

import { QUORUM_LOCALES } from './i18n'

function leafPaths(value: unknown, prefix = ''): string[] {
  if (!value || typeof value !== 'object') {
    return [prefix]
  }

  return Object.entries(value)
    .flatMap(([key, child]) => leafPaths(child, prefix ? `${prefix}.${key}` : key))
    .sort()
}

describe('Quorum plugin locales', () => {
  it('keeps every supported locale structurally complete', () => {
    const english = leafPaths(QUORUM_LOCALES.en)

    for (const locale of ['ja', 'zh', 'zh-hant'] as const) {
      expect(leafPaths(QUORUM_LOCALES[locale])).toEqual(english)
    }
  })
})
