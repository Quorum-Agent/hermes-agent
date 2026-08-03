import path from 'node:path'

import { PRODUCT_IDENTITY } from '../product-identity.mjs'

export interface GitBashOptions {
  isWindows: boolean
  env: Record<string, string | undefined>
  managedHome?: string
  fileExists: (filePath: string) => boolean
  findOnPath?: (command: string) => string | null
}

/**
 * Locate bash.exe on Windows.
 * Resolution order (first match wins):
 *   1. HERMES_GIT_BASH_PATH env var override
 *   2. PortableGit under the active Quorum managed home (install.ps1)
 *   3. Standard Git for Windows install locations
 *   4. %LOCALAPPDATA%\Programs\Git\ (user-scoped)
 *   5. bash on PATH
 */
export function findGitBash(opts: GitBashOptions): string | null {
  const { isWindows, env, fileExists, findOnPath, managedHome } = opts

  if (!isWindows) {
    return findOnPath ? findOnPath('bash') : null
  }

  // Respect HERMES_GIT_BASH_PATH if set (mirrors tools/environments/local.py:_find_bash).
  const gitBashPath = env.HERMES_GIT_BASH_PATH

  if (gitBashPath && fileExists(gitBashPath)) {
    return gitBashPath
  }

  const localAppData = env.LOCALAPPDATA || ''
  const candidates: string[] = []

  // Candidate paths are Windows paths regardless of host platform (tests run
  // on POSIX CI hosts too), so join with win32 semantics explicitly.
  const joinWin = path.win32.join

  const addManagedGit = (home: string) => {
    if (!home) {
      return
    }

    candidates.push(joinWin(home, 'git', 'bin', 'bash.exe'))
    candidates.push(joinWin(home, 'git', 'usr', 'bin', 'bash.exe'))
  }

  addManagedGit(managedHome || '')

  if (localAppData) {
    addManagedGit(joinWin(localAppData, PRODUCT_IDENTITY.windowsHomeDirName))
  }

  candidates.push(joinWin(env['ProgramFiles'] || 'C:\\Program Files', 'Git', 'bin', 'bash.exe'))
  candidates.push(joinWin(env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)', 'Git', 'bin', 'bash.exe'))

  if (localAppData) {
    candidates.push(joinWin(localAppData, 'Programs', 'Git', 'bin', 'bash.exe'))
  }

  // Explorer-launched Electron can have a stale process PATH immediately
  // after install.ps1 updates the User environment. main.ts supplies the live
  // HKCU value; inspect it directly instead of relying only on process lookup.
  const livePath = env.Path || env.PATH || ''

  for (const segment of livePath.split(';')) {
    const directory = segment.trim().replace(/^"|"$/g, '')

    if (directory) {
      candidates.push(joinWin(directory, 'bash.exe'))
    }
  }

  for (const candidate of [...new Set(candidates.map(candidate => path.win32.normalize(candidate)))]) {
    if (fileExists(candidate)) {
      return candidate
    }
  }

  if (findOnPath) {
    const onPath = findOnPath('bash')

    if (onPath) {
      return onPath
    }
  }

  return null
}
