import assert from 'node:assert/strict'
import fs from 'node:fs'

import { test } from 'vitest'

import { PRODUCT_IDENTITY } from '../product-identity.mjs'

test('Quorum desktop identity has independent OS and state namespaces', () => {
  assert.equal(PRODUCT_IDENTITY.editionId, 'quorum')
  assert.notEqual(PRODUCT_IDENTITY.homeDirName, '.hermes')
  assert.notEqual(PRODUCT_IDENTITY.windowsHomeDirName.toLowerCase(), 'hermes')
  assert.notEqual(PRODUCT_IDENTITY.protocol, 'hermes')
  assert.notEqual(PRODUCT_IDENTITY.appId, 'com.nousresearch.hermes')
})

test('bootstrap and update authority derive from the same Quorum repository', () => {
  const source = new URL(PRODUCT_IDENTITY.sourceRepoHttpsUrl)
  const canonical = `${source.hostname}${source.pathname.replace(/\.git$/, '')}`.toLowerCase()

  assert.equal(canonical, PRODUCT_IDENTITY.sourceRepoCanonical)
  assert.equal(
    PRODUCT_IDENTITY.rawSourceBaseUrl,
    `https://raw.githubusercontent.com/${PRODUCT_IDENTITY.sourceRepository}`
  )
  assert.match(PRODUCT_IDENTITY.upstreamAttribution, /Hermes Agent by Nous Research/)
})

test('main-process fallbacks use Quorum identity and managed state', () => {
  const source = fs.readFileSync(new URL('./main.ts', import.meta.url), 'utf8')

  assert.doesNotMatch(source, /path\.join\(localAppData, 'hermes', 'git'/)
  assert.doesNotMatch(source, /title: 'Hermes'/)
  assert.match(source, /title: APP_NAME/)
  assert.match(source, /title: payload\?\.title \|\| PRODUCT_IDENTITY\.productName/)
})
