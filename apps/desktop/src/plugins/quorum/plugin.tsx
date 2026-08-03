import {
  type HermesPlugin,
  host,
  PALETTE_AREA,
  type PaletteContribution,
  type RouteContribution,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution,
  STATUSBAR_AREAS,
  type StatusbarItem,
  usePluginI18n
} from '@hermes/plugin-sdk'

import { bindApi } from './api'
import { QUORUM_LOCALES } from './i18n'
import { QuorumPage, QuorumStatusDetail, QuorumStatusIcon } from './page'

function QuorumStatusLabel() {
  const t = usePluginI18n('quorum')

  return <>{t('title')}</>
}

const plugin: HermesPlugin = {
  id: 'quorum',
  name: 'Quorum',
  defaultEnabled: true,
  register(ctx) {
    ctx.i18n.register(QUORUM_LOCALES)
    ctx.onDispose(bindApi(ctx.rest))

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/quorum' } satisfies RouteContribution,
        render: () => <QuorumPage />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 60,
        data: { codicon: 'shield', label: 'Quorum', path: '/quorum' } satisfies SidebarNavContribution
      },
      {
        id: 'status',
        area: STATUSBAR_AREAS.right,
        order: 75,
        data: {
          detail: <QuorumStatusDetail />,
          icon: <QuorumStatusIcon />,
          id: 'quorum',
          label: <QuorumStatusLabel />,
          lockedVisible: true,
          to: '/quorum',
          variant: 'action'
        } satisfies StatusbarItem
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'quorum.open',
          label: ctx.i18n.t('openInspector'),
          keywords: ['quorum', 'routing', 'policy', 'privacy', 'inspector'],
          run: () => host.navigate('/quorum')
        } satisfies PaletteContribution
      }
    ])
  }
}

export default plugin
