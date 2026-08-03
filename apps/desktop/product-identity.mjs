/**
 * Quorum Edition's browser-safe desktop identity and source authority.
 *
 * Python runtime code consumes the matching stdlib-only `product_identity.py`.
 * Electron and the renderer both import this runtime module so protocols,
 * repository authority, and state namespaces cannot drift between processes.
 */

export const PRODUCT_IDENTITY = Object.freeze({
  editionId: 'quorum',
  productName: 'Quorum',
  productSlug: 'quorum',
  homeDirName: '.quorum',
  windowsHomeDirName: 'quorum',
  appId: 'org.quorumagent.quorum',
  protocol: 'quorum',
  mediaProtocol: 'quorum-media',
  sourceRepository: 'Quorum-Agent/hermes-agent',
  sourceRepoHttpsUrl: 'https://github.com/Quorum-Agent/hermes-agent.git',
  sourceRepoCanonical: 'github.com/quorum-agent/hermes-agent',
  rawSourceBaseUrl: 'https://raw.githubusercontent.com/Quorum-Agent/hermes-agent',
  upstreamAttribution: 'Based on Hermes Agent by Nous Research'
})
