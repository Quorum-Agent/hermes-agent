import assert from 'node:assert/strict'

import { test } from 'vitest'

import { findGitBash } from './find-git-bash'

const yes = () => true
const no = () => false

test('HERMES_GIT_BASH_PATH override takes precedence', () => {
  const result = findGitBash({
    isWindows: true,
    env: { HERMES_GIT_BASH_PATH: 'D:\\CustomGit\\bin\\bash.exe' },
    fileExists: yes,
    findOnPath: () => null
  })

  assert.equal(result, 'D:\\CustomGit\\bin\\bash.exe')
})

test('HERMES_GIT_BASH_PATH invalid path falls through to candidates', () => {
  const env = {
    HERMES_GIT_BASH_PATH: 'X:\\Missing\\bash.exe',
    LOCALAPPDATA: 'C:\\Users\\test\\AppData\\Local',
    ProgramFiles: 'C:\\Program Files',
    'ProgramFiles(x86)': 'C:\\Program Files (x86)'
  }

  const fileExists = (p: string) => p !== 'X:\\Missing\\bash.exe' && p.includes('Program Files\\Git\\bin\\bash.exe')
  const result = findGitBash({ isWindows: true, env, fileExists, findOnPath: () => null })
  assert.equal(result, 'C:\\Program Files\\Git\\bin\\bash.exe')
})

test('HERMES_GIT_BASH_PATH empty string is ignored', () => {
  const result = findGitBash({
    isWindows: true,
    env: { HERMES_GIT_BASH_PATH: '', LOCALAPPDATA: '' },
    fileExists: no,
    findOnPath: () => 'C:\\msys64\\usr\\bin\\bash.exe'
  })

  assert.equal(result, 'C:\\msys64\\usr\\bin\\bash.exe')
})

test('fresh Quorum install finds PortableGit from the active managed home despite stale process env', () => {
  const managedHome = 'D:\\Profiles\\writer\\.quorum'
  const portable = `${managedHome}\\git\\bin\\bash.exe`

  const result = findGitBash({
    isWindows: true,
    env: {
      LOCALAPPDATA: 'C:\\Users\\test\\AppData\\Local',
      PATH: 'C:\\Windows\\System32'
    },
    managedHome,
    fileExists: candidate => candidate === portable,
    findOnPath: () => null
  })

  assert.equal(result, portable)
})

test('live User PATH is searched when the Electron process PATH is stale', () => {
  const portable = 'C:\\Users\\test\\AppData\\Local\\quorum\\git\\bin\\bash.exe'

  const result = findGitBash({
    isWindows: true,
    env: { Path: 'C:\\Windows\\System32;C:\\Users\\test\\AppData\\Local\\quorum\\git\\bin' },
    fileExists: candidate => candidate === portable,
    findOnPath: () => null
  })

  assert.equal(result, portable)
})

test('non-Windows uses findOnPath', () => {
  const result = findGitBash({
    isWindows: false,
    env: {},
    fileExists: no,
    findOnPath: () => '/usr/bin/bash'
  })

  assert.equal(result, '/usr/bin/bash')
})
