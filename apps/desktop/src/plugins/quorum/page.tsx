import {
  Button,
  cn,
  EmptyState,
  ErrorState,
  fmtDateTime,
  host,
  icons,
  Loader,
  SegmentedControl,
  StatusDot,
  Switch,
  Tip,
  useMutation,
  useQuery,
  useQueryClient,
  useValue
} from '@hermes/plugin-sdk'
import { useEffect, useMemo, useState } from 'react'

import { fetchOverview, overviewKey, saveSettings } from './api'
import { useQuorum } from './i18n'
import type { QuorumEvent, QuorumOverview, QuorumPolicy } from './types'

const POLICIES: readonly QuorumPolicy[] = ['private', 'balanced', 'quality', 'offline']

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function eventTime(event: QuorumEvent): string | null {
  const raw = event.occurred_at || event.timestamp

  if (!raw) {
    return null
  }

  const parsed = typeof raw === 'number' ? raw * 1000 : Date.parse(raw)

  return Number.isFinite(parsed) ? fmtDateTime.format(parsed) : String(raw)
}

function EventRow({ event }: { event: QuorumEvent }) {
  const copy = useQuorum()

  const title =
    event.allowed === true
      ? copy.allowed
      : event.allowed === false
        ? copy.blocked
        : event.decision || event.kind || copy.eventFallback

  const target = [event.provider, event.model].filter(Boolean).join(' / ')
  const time = eventTime(event)

  return (
    <li className="grid gap-1 py-2 text-xs">
      <div className="flex min-w-0 items-center justify-between gap-3">
        <span className="truncate font-medium text-(--ui-text-primary)">{title}</span>
        {time && <time className="shrink-0 text-(--ui-text-tertiary)">{time}</time>}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-(--ui-text-secondary)">
        {event.policy && (
          <span>
            {copy.policy}: <span className="text-(--ui-text-primary)">{event.policy}</span>
          </span>
        )}
        {target && (
          <span>
            {event.provider ? copy.provider : copy.model}: <span className="text-(--ui-text-primary)">{target}</span>
          </span>
        )}
        {!!event.sensitive_categories?.length && (
          <span>
            {copy.sensitive}: <span className="text-(--ui-text-primary)">{event.sensitive_categories.join(', ')}</span>
          </span>
        )}
      </div>
      {(event.reason || event.error) && (
        <p className={cn('line-clamp-3 text-(--ui-text-tertiary)', event.error && 'text-destructive')}>
          {copy.reason}: {event.reason || event.error}
        </p>
      )}
    </li>
  )
}

function EnforcementSummary({ overview }: { overview: QuorumOverview }) {
  const copy = useQuorum()
  const available = overview.status.available !== false

  return (
    <section className="grid gap-3 border-b border-(--ui-stroke-tertiary) pb-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold">{copy.runtime}</h2>
          <p className="mt-1 text-xs leading-5 text-(--ui-text-secondary)">
            {available ? copy.enforcementActive : copy.enforcementUnavailable}
          </p>
        </div>
        <span className="flex shrink-0 items-center gap-2 text-xs font-medium">
          <StatusDot tone={available ? 'good' : 'bad'} />
          {available ? copy.statusActive : copy.statusUnavailable}
        </span>
      </div>
      {!available && overview.status.reason && (
        <code className="w-fit text-[0.6875rem] text-(--ui-text-tertiary)">{overview.status.reason}</code>
      )}
    </section>
  )
}

function Settings({ overview }: { overview: QuorumOverview }) {
  const copy = useQuorum()
  const client = useQueryClient()
  const profile = useValue(host.state.profile)
  const [policy, setPolicy] = useState<QuorumPolicy>(overview.settings.default_policy)
  const [cloudConsent, setCloudConsent] = useState(overview.settings.cloud_consent)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!dirty) {
      setPolicy(overview.settings.default_policy)
      setCloudConsent(overview.settings.cloud_consent)
    }
  }, [dirty, overview.settings.cloud_consent, overview.settings.default_policy])

  const save = useMutation({
    mutationFn: () => saveSettings({ default_policy: policy, cloud_consent: cloudConsent }),
    onError: error => host.notify({ kind: 'error', message: errorText(error) }),
    onSuccess: settings => {
      client.setQueryData<QuorumOverview>(overviewKey(profile), current =>
        current ? { ...current, settings } : current
      )
      setDirty(false)
      host.notify({ kind: 'success', message: copy.saved })
    }
  })

  const options = useMemo(() => POLICIES.map(id => ({ id, label: copy.policyOptions[id] })), [copy.policyOptions])

  return (
    <section className="grid gap-5 border-b border-(--ui-stroke-tertiary) py-6">
      <h2 className="text-sm font-semibold">{copy.settings}</h2>
      <div className="grid gap-2">
        <span className="text-xs font-medium">{copy.policy}</span>
        <SegmentedControl
          disabled={save.isPending}
          onChange={next => {
            setPolicy(next)
            setDirty(true)
          }}
          options={options}
          value={policy}
        />
        <p className="max-w-2xl text-xs leading-5 text-(--ui-text-tertiary)">{copy.policyHelp}</p>
      </div>
      <div className="flex max-w-2xl items-start justify-between gap-4">
        <div>
          <div className="text-xs font-medium">{copy.cloudConsent}</div>
          <p className="mt-1 text-xs leading-5 text-(--ui-text-tertiary)">{copy.cloudConsentHelp}</p>
        </div>
        <Switch
          aria-label={copy.cloudConsent}
          checked={cloudConsent}
          disabled={save.isPending}
          onCheckedChange={next => {
            setCloudConsent(next)
            setDirty(true)
          }}
          size="xs"
        />
      </div>
      <div>
        <Button disabled={!dirty || save.isPending} onClick={() => save.mutate()} size="sm">
          {save.isPending ? copy.saving : copy.save}
        </Button>
      </div>
    </section>
  )
}

function Events({ overview }: { overview: QuorumOverview }) {
  const copy = useQuorum()

  return (
    <section className="grid gap-4 pt-6">
      <div>
        <h2 className="text-sm font-semibold">{copy.events}</h2>
        <p className="mt-1 text-xs text-(--ui-text-tertiary)">{copy.eventsProcessLocal}</p>
      </div>
      {!overview.inspection.available ? (
        <EmptyState description={copy.inspectorUnavailableHelp} title={copy.inspectorUnavailable} />
      ) : overview.inspection.events.length === 0 ? (
        <EmptyState description={copy.emptyEventsHelp} title={copy.emptyEvents} />
      ) : (
        <ul className="grid gap-2">
          {overview.inspection.events.map((event, index) => (
            <EventRow
              event={event}
              key={String(event.id ?? `${event.timestamp ?? event.occurred_at ?? 'event'}:${index}`)}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

export function QuorumPage() {
  const copy = useQuorum()
  const profile = useValue(host.state.profile)

  const overview = useQuery({
    queryFn: () => fetchOverview(),
    queryKey: overviewKey(profile),
    refetchInterval: 15_000
  })

  return (
    <div className="h-full overflow-y-auto bg-(--ui-surface-background)">
      <main className="mx-auto grid w-full max-w-5xl gap-0 px-6 py-8">
        <header className="mb-8 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{copy.title}</h1>
            <p className="mt-1 text-sm leading-5 text-(--ui-text-secondary)">{copy.subtitle}</p>
          </div>
          <Tip label={copy.refresh}>
            <Button
              aria-label={copy.refresh}
              disabled={overview.isFetching}
              onClick={() => void overview.refetch()}
              size="icon-sm"
              variant="ghost"
            >
              <icons.RefreshCw />
            </Button>
          </Tip>
        </header>

        {overview.isPending ? (
          <div className="grid min-h-64 place-items-center">
            <Loader type="lemniscate-bloom" />
          </div>
        ) : overview.error || !overview.data ? (
          <ErrorState
            className="mx-auto max-w-lg py-16"
            description={overview.error ? errorText(overview.error) : copy.inspectorUnavailableHelp}
            title={copy.inspectorUnavailable}
          >
            <Button onClick={() => void overview.refetch()} variant="secondary">
              {copy.refresh}
            </Button>
          </ErrorState>
        ) : (
          <>
            <EnforcementSummary overview={overview.data} />
            <Settings overview={overview.data} />
            <Events overview={overview.data} />
          </>
        )}
      </main>
    </div>
  )
}

function useStatusOverview() {
  const profile = useValue(host.state.profile)

  return useQuery({
    queryFn: () => fetchOverview(),
    queryKey: overviewKey(profile),
    refetchInterval: 15_000
  })
}

export function QuorumStatusDetail() {
  const copy = useQuorum()
  const { data, error } = useStatusOverview()

  return (
    <>
      {!data
        ? error
          ? copy.statusUnavailable
          : copy.statusChecking
        : data.status.available === false
          ? copy.statusUnavailable
          : copy.statusActive}
    </>
  )
}

export function QuorumStatusIcon() {
  const { data, error } = useStatusOverview()
  const tone = !data ? (error ? 'bad' : 'muted') : data.status.available === false ? 'bad' : 'good'

  return <StatusDot tone={tone} />
}
