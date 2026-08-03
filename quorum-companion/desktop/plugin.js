/** Quorum Companion runtime plugin for stock Hermes Desktop.
 *
 * This is intentionally a visibility surface, not an enforcement shim. The
 * stock host does not offer a fail-closed dispatch boundary to runtime plugins.
 */
import React from 'react'
import {
  host,
  PALETTE_AREA,
  STATUSBAR_AREAS,
  usePluginI18n
} from '@hermes/plugin-sdk'

const LOCALES = {
  en: {
    detail: 'best effort',
    inspect: 'Quorum Companion: Check host guard',
    label: 'Quorum Companion',
    unavailable: 'Quorum Edition enforcement is unavailable. Companion is best-effort only.',
    available: 'Quorum Edition host guard detected. Open Quorum Edition for the full inspector.'
  },
  ja: {
    detail: 'ベストエフォート',
    inspect: 'Quorum Companion：ホストガードを確認',
    label: 'Quorum Companion',
    unavailable: 'Quorum Edition の強制機能は利用できません。Companion はベストエフォートのみです。',
    available: 'Quorum Edition のホストガードを検出しました。完全なインスペクターは Quorum Edition で開いてください。'
  },
  zh: {
    detail: '尽力而为',
    inspect: 'Quorum Companion：检查主机防护',
    label: 'Quorum Companion',
    unavailable: 'Quorum Edition 强制功能不可用。Companion 仅提供尽力而为的能力。',
    available: '检测到 Quorum Edition 主机防护。请在 Quorum Edition 中使用完整检查器。'
  },
  'zh-hant': {
    detail: '盡力而為',
    inspect: 'Quorum Companion：檢查主機防護',
    label: 'Quorum Companion',
    unavailable: 'Quorum Edition 強制功能無法使用。Companion 僅提供盡力而為的能力。',
    available: '偵測到 Quorum Edition 主機防護。請在 Quorum Edition 中使用完整檢查器。'
  }
}

function CompanionLabel() {
  const t = usePluginI18n('quorum')
  return React.createElement(React.Fragment, null, t('label'))
}

function CompanionDetail() {
  const t = usePluginI18n('quorum')
  return React.createElement(React.Fragment, null, t('detail'))
}

const plugin = {
  id: 'quorum',
  name: 'Quorum Companion',
  defaultEnabled: true,
  register(ctx) {
    ctx.i18n.register(LOCALES)

    const inspect = async () => {
      try {
        const overview = await ctx.rest('/overview?limit=1')
        const available = overview?.status?.available === true
        host.notify({ kind: available ? 'info' : 'warning', message: ctx.i18n.t(available ? 'available' : 'unavailable') })
      } catch {
        host.notify({ kind: 'warning', message: ctx.i18n.t('unavailable') })
      }
    }

    ctx.registerMany([
      {
        id: 'status',
        area: STATUSBAR_AREAS.right,
        order: 75,
        data: {
          detail: React.createElement(CompanionDetail),
          id: 'quorum-companion',
          label: React.createElement(CompanionLabel),
          onSelect: () => void inspect(),
          variant: 'action'
        }
      },
      {
        id: 'inspect',
        area: PALETTE_AREA,
        data: {
          id: 'quorum-companion.inspect',
          label: ctx.i18n.t('inspect'),
          keywords: ['quorum', 'companion', 'routing', 'privacy'],
          run: () => void inspect()
        }
      }
    ])
  }
}

export default plugin
