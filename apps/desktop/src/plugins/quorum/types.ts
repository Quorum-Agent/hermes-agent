export type QuorumPolicy = 'balanced' | 'offline' | 'private' | 'quality'

export interface QuorumSettings {
  cloud_consent: boolean
  default_policy: QuorumPolicy
  session_override_count: number
}

export interface QuorumStatus {
  available?: boolean
  default_policy?: string
  decisions_observed?: number
  enforcement?: string
  events_durable?: boolean
  mode?: string
  reason?: string
  [key: string]: boolean | null | number | string | undefined
}

export interface QuorumEvent {
  allowed?: boolean
  call_role?: string
  decision?: string
  error?: string
  id?: number | string
  kind?: string
  model?: string
  occurred_at?: string
  policy?: string
  provider?: string
  reach?: string
  reason?: string
  sensitive_categories?: string[]
  session_id?: string
  timestamp?: number | string
}

export interface QuorumInspection {
  available: boolean
  durable: false
  events: QuorumEvent[]
  next_before?: null | number | string
  reason?: string
}

export interface QuorumOverview {
  enforcement_controlled_by_host: true
  inspection: QuorumInspection
  settings: QuorumSettings
  status: QuorumStatus
}
