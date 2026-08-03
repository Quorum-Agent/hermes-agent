export interface ProductIdentity {
  readonly editionId: 'quorum'
  readonly productName: 'Quorum'
  readonly productSlug: 'quorum'
  readonly homeDirName: '.quorum'
  readonly windowsHomeDirName: 'quorum'
  readonly appId: 'org.quorumagent.quorum'
  readonly protocol: 'quorum'
  readonly mediaProtocol: 'quorum-media'
  readonly sourceRepository: 'Quorum-Agent/hermes-agent'
  readonly sourceRepoHttpsUrl: 'https://github.com/Quorum-Agent/hermes-agent.git'
  readonly sourceRepoCanonical: 'github.com/quorum-agent/hermes-agent'
  readonly rawSourceBaseUrl: 'https://raw.githubusercontent.com/Quorum-Agent/hermes-agent'
  readonly upstreamAttribution: 'Based on Hermes Agent by Nous Research'
}

export const PRODUCT_IDENTITY: Readonly<ProductIdentity>
