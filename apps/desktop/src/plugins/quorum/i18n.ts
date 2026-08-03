import { type PluginLocaleBundles, type PluginMessages, usePluginI18n } from '@hermes/plugin-sdk'
import { useMemo } from 'react'

import type { QuorumPolicy } from './types'

interface PolicyMessages extends PluginMessages, Record<QuorumPolicy, string> {}

export interface QuorumMessages extends PluginMessages {
  allowed: string
  blocked: string
  cloudConsent: string
  cloudConsentHelp: string
  decision: string
  emptyEvents: string
  emptyEventsHelp: string
  enforcementActive: string
  enforcementUnavailable: string
  eventFallback: string
  events: string
  eventsProcessLocal: string
  inspectorUnavailable: string
  inspectorUnavailableHelp: string
  model: string
  nav: string
  openInspector: string
  policy: string
  policyHelp: string
  policyOptions: PolicyMessages
  provider: string
  reason: string
  refresh: string
  runtime: string
  save: string
  saved: string
  saving: string
  sensitive: string
  settings: string
  statusActive: string
  statusChecking: string
  statusUnavailable: string
  subtitle: string
  title: string
}

const en: QuorumMessages = {
  allowed: 'Allowed',
  blocked: 'Blocked',
  cloudConsent: 'Allow cloud routing',
  cloudConsentHelp: 'Cloud-capable policies remain local until you explicitly allow remote providers.',
  decision: 'Decision',
  emptyEvents: 'No decisions observed in this process',
  emptyEventsHelp: 'New routing decisions appear here while this backend process is running.',
  enforcementActive: 'The host dispatch guard is active and cannot be disabled from this plugin.',
  enforcementUnavailable: 'The host dispatch guard is unavailable in this runtime.',
  eventFallback: 'Routing decision',
  events: 'Recent decisions',
  eventsProcessLocal: 'Process memory only — this is not a durable audit ledger.',
  inspectorUnavailable: 'Inspector unavailable',
  inspectorUnavailableHelp: 'Reconnect to a Quorum Edition backend to inspect routing decisions.',
  model: 'Model',
  nav: 'Quorum',
  openInspector: 'Quorum: Open inspector',
  policy: 'Default policy',
  policyHelp: 'Applied when a session has no explicit override. Enforcement takes effect at dispatch.',
  policyOptions: { balanced: 'Balanced', offline: 'Offline', private: 'Private', quality: 'Quality' },
  provider: 'Provider',
  reason: 'Reason',
  refresh: 'Refresh',
  runtime: 'Runtime enforcement',
  save: 'Save settings',
  saved: 'Quorum settings saved',
  saving: 'Saving…',
  sensitive: 'Sensitive data',
  settings: 'Policy settings',
  statusActive: 'Active',
  statusChecking: 'Checking',
  statusUnavailable: 'Unavailable',
  subtitle: 'Configure routing policy and inspect process-local dispatch decisions.',
  title: 'Quorum'
}

const ja: QuorumMessages = {
  allowed: '許可',
  blocked: 'ブロック',
  cloudConsent: 'クラウドルーティングを許可',
  cloudConsentHelp: 'リモートプロバイダーを明示的に許可するまで、クラウド対応ポリシーもローカルに留まります。',
  decision: '判定',
  emptyEvents: 'このプロセスではまだ判定がありません',
  emptyEventsHelp: 'このバックエンドプロセスの実行中、新しいルーティング判定がここに表示されます。',
  enforcementActive: 'ホストのディスパッチガードは有効で、このプラグインから無効化できません。',
  enforcementUnavailable: 'このランタイムではホストのディスパッチガードを利用できません。',
  eventFallback: 'ルーティング判定',
  events: '最近の判定',
  eventsProcessLocal: 'プロセスメモリのみ — 永続監査台帳ではありません。',
  inspectorUnavailable: 'インスペクターを利用できません',
  inspectorUnavailableHelp: 'Quorum Edition バックエンドに再接続するとルーティング判定を確認できます。',
  model: 'モデル',
  nav: 'Quorum',
  openInspector: 'Quorum：インスペクターを開く',
  policy: '既定のポリシー',
  policyHelp: 'セッションに明示的な上書きがない場合に適用されます。実行時に強制されます。',
  policyOptions: { balanced: 'バランス', offline: 'オフライン', private: 'プライベート', quality: '品質優先' },
  provider: 'プロバイダー',
  reason: '理由',
  refresh: '更新',
  runtime: 'ランタイム強制',
  save: '設定を保存',
  saved: 'Quorum の設定を保存しました',
  saving: '保存中…',
  sensitive: '機密データ',
  settings: 'ポリシー設定',
  statusActive: '有効',
  statusChecking: '確認中',
  statusUnavailable: '利用不可',
  subtitle: 'ルーティングポリシーを設定し、プロセス内のディスパッチ判定を確認します。',
  title: 'Quorum'
}

const zh: QuorumMessages = {
  allowed: '已允许',
  blocked: '已阻止',
  cloudConsent: '允许云端路由',
  cloudConsentHelp: '在你明确允许远程提供商之前，支持云端的策略仍只会使用本地路由。',
  decision: '决策',
  emptyEvents: '此进程尚未观察到决策',
  emptyEventsHelp: '此后端进程运行期间，新的路由决策会显示在这里。',
  enforcementActive: '主机调度防护已启用，且无法从此插件中关闭。',
  enforcementUnavailable: '此运行时没有可用的主机调度防护。',
  eventFallback: '路由决策',
  events: '最近决策',
  eventsProcessLocal: '仅保存在进程内存中 — 这不是持久审计账本。',
  inspectorUnavailable: '检查器不可用',
  inspectorUnavailableHelp: '重新连接到 Quorum Edition 后端以检查路由决策。',
  model: '模型',
  nav: 'Quorum',
  openInspector: 'Quorum：打开检查器',
  policy: '默认策略',
  policyHelp: '当会话没有显式覆盖时应用。策略会在实际调度时执行。',
  policyOptions: { balanced: '平衡', offline: '离线', private: '私密', quality: '质量优先' },
  provider: '提供商',
  reason: '原因',
  refresh: '刷新',
  runtime: '运行时强制',
  save: '保存设置',
  saved: 'Quorum 设置已保存',
  saving: '正在保存…',
  sensitive: '敏感数据',
  settings: '策略设置',
  statusActive: '已启用',
  statusChecking: '正在检查',
  statusUnavailable: '不可用',
  subtitle: '配置路由策略并检查进程内的调度决策。',
  title: 'Quorum'
}

const zhHant: QuorumMessages = {
  allowed: '已允許',
  blocked: '已阻擋',
  cloudConsent: '允許雲端路由',
  cloudConsentHelp: '在你明確允許遠端供應商之前，支援雲端的策略仍只會使用本機路由。',
  decision: '決策',
  emptyEvents: '此程序尚未觀察到決策',
  emptyEventsHelp: '此後端程序執行期間，新的路由決策會顯示在這裡。',
  enforcementActive: '主機派送防護已啟用，且無法從此外掛程式中關閉。',
  enforcementUnavailable: '此執行環境沒有可用的主機派送防護。',
  eventFallback: '路由決策',
  events: '最近決策',
  eventsProcessLocal: '僅保存在程序記憶體中 — 這不是持久稽核帳本。',
  inspectorUnavailable: '檢查器無法使用',
  inspectorUnavailableHelp: '重新連線到 Quorum Edition 後端以檢查路由決策。',
  model: '模型',
  nav: 'Quorum',
  openInspector: 'Quorum：開啟檢查器',
  policy: '預設策略',
  policyHelp: '當工作階段沒有明確覆寫時套用。策略會在實際派送時執行。',
  policyOptions: { balanced: '平衡', offline: '離線', private: '私密', quality: '品質優先' },
  provider: '供應商',
  reason: '原因',
  refresh: '重新整理',
  runtime: '執行階段強制',
  save: '儲存設定',
  saved: 'Quorum 設定已儲存',
  saving: '正在儲存…',
  sensitive: '敏感資料',
  settings: '策略設定',
  statusActive: '已啟用',
  statusChecking: '正在檢查',
  statusUnavailable: '無法使用',
  subtitle: '設定路由策略並檢查程序內的派送決策。',
  title: 'Quorum'
}

export const QUORUM_LOCALES: PluginLocaleBundles = { en, ja, zh, 'zh-hant': zhHant }

export function useQuorum(): QuorumMessages {
  const t = usePluginI18n('quorum')

  return useMemo(
    () => ({
      allowed: t('allowed'),
      blocked: t('blocked'),
      cloudConsent: t('cloudConsent'),
      cloudConsentHelp: t('cloudConsentHelp'),
      decision: t('decision'),
      emptyEvents: t('emptyEvents'),
      emptyEventsHelp: t('emptyEventsHelp'),
      enforcementActive: t('enforcementActive'),
      enforcementUnavailable: t('enforcementUnavailable'),
      eventFallback: t('eventFallback'),
      events: t('events'),
      eventsProcessLocal: t('eventsProcessLocal'),
      inspectorUnavailable: t('inspectorUnavailable'),
      inspectorUnavailableHelp: t('inspectorUnavailableHelp'),
      model: t('model'),
      nav: t('nav'),
      openInspector: t('openInspector'),
      policy: t('policy'),
      policyHelp: t('policyHelp'),
      policyOptions: {
        balanced: t('policyOptions.balanced'),
        offline: t('policyOptions.offline'),
        private: t('policyOptions.private'),
        quality: t('policyOptions.quality')
      },
      provider: t('provider'),
      reason: t('reason'),
      refresh: t('refresh'),
      runtime: t('runtime'),
      save: t('save'),
      saved: t('saved'),
      saving: t('saving'),
      sensitive: t('sensitive'),
      settings: t('settings'),
      statusActive: t('statusActive'),
      statusChecking: t('statusChecking'),
      statusUnavailable: t('statusUnavailable'),
      subtitle: t('subtitle'),
      title: t('title')
    }),
    [t]
  )
}
