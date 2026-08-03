import assert from 'node:assert/strict'

import { test } from 'vitest'

import { resolveQuorumManagedHome } from './quorum-home'

test('ambient stock HERMES_HOME never redirects Quorum Desktop state', () => {
  assert.equal(
    resolveQuorumManagedHome({
      env: { HERMES_HOME: '/Users/writer/.hermes' },
      homePath: '/Users/writer'
    }),
    '/Users/writer/.quorum'
  )
})

test('Windows ignores stock HERMES_HOME and chooses LOCALAPPDATA quorum', () => {
  assert.equal(
    resolveQuorumManagedHome({
      env: { HERMES_HOME: 'C:\\Users\\writer\\.hermes', LOCALAPPDATA: 'C:\\Users\\writer\\AppData\\Local' },
      isWindows: true,
      homePath: 'C:\\Users\\writer'
    }),
    'C:\\Users\\writer\\AppData\\Local\\quorum'
  )
})

test('QUORUM_HOME is explicit but stock roots require migration opt-in', () => {
  assert.throws(
    () =>
      resolveQuorumManagedHome({
        env: { QUORUM_HOME: '/Users/writer/.hermes' },
        homePath: '/Users/writer'
      }),
    /intentional migration/
  )
  assert.equal(
    resolveQuorumManagedHome({
      env: {
        QUORUM_HOME: '/Users/writer/.hermes',
        QUORUM_ALLOW_HERMES_HOME_MIGRATION: '1'
      },
      homePath: '/Users/writer'
    }),
    '/Users/writer/.hermes'
  )
})

test('legacy HERMES_HOME is adopted only with explicit migration opt-in', () => {
  assert.equal(
    resolveQuorumManagedHome({
      env: { HERMES_HOME: '/srv/migrated', QUORUM_ALLOW_HERMES_HOME_MIGRATION: '1' },
      homePath: '/Users/writer'
    }),
    '/srv/migrated'
  )
})
