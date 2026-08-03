import { type PluginRestOptions } from '@hermes/plugin-sdk'

import type { QuorumOverview, QuorumSettings } from './types'

type Rest = <T>(path: string, opts?: PluginRestOptions) => Promise<T>

let rest: null | Rest = null

export function bindApi(next: Rest): () => void {
  rest = next

  return () => {
    if (rest === next) {
      rest = null
    }
  }
}

function call<T>(path: string, opts?: PluginRestOptions): Promise<T> {
  return rest ? rest<T>(path, opts) : Promise.reject(new Error('Quorum API not ready'))
}

export const overviewKey = (profile: string) => ['quorum', 'overview', profile] as const

export const fetchOverview = (limit = 50) => call<QuorumOverview>(`/overview?limit=${limit}`)

export const saveSettings = (settings: Pick<QuorumSettings, 'cloud_consent' | 'default_policy'>) =>
  call<QuorumSettings>('/settings', {
    method: 'PUT',
    body: settings
  })
