import { afterEach, describe, expect, it, vi } from 'vitest'

import { bindApi, fetchOverview, saveSettings } from './api'

describe('Quorum plugin API', () => {
  let dispose: null | (() => void) = null

  afterEach(() => {
    dispose?.()
    dispose = null
  })

  it('uses only the plugin-scoped overview and settings routes', async () => {
    const rest = vi.fn().mockResolvedValue({})
    dispose = bindApi(rest)

    await fetchOverview(12)
    await saveSettings({ cloud_consent: false, default_policy: 'private' })

    expect(rest).toHaveBeenNthCalledWith(1, '/overview?limit=12', undefined)
    expect(rest).toHaveBeenNthCalledWith(2, '/settings', {
      body: { cloud_consent: false, default_policy: 'private' },
      method: 'PUT'
    })
  })

  it('tears down the bound API when the plugin unloads', async () => {
    dispose = bindApi(vi.fn().mockResolvedValue({}))
    dispose()
    dispose = null

    await expect(fetchOverview()).rejects.toThrow('Quorum API not ready')
  })
})
