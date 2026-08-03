import path from 'node:path'

import { PRODUCT_IDENTITY } from '../product-identity.mjs'

function migrationAllowed(env) {
  return String(env.QUORUM_ALLOW_HERMES_HOME_MIGRATION || '').trim() === '1'
}

function samePath(left, right, pathModule) {
  if (!left || !right) {
    return false
  }

  const normalize = value =>
    pathModule
      .resolve(String(value))
      .replace(/[\\/]+$/, '')
      .toLowerCase()

  return normalize(left) === normalize(right)
}

export function resolveQuorumManagedHome(options: any) {
  const {
    env = {},
    isWindows = false,
    userDataOverride = '',
    homePath,
    localAppData = env.LOCALAPPDATA,
    readWindowsUserEnvVar = () => null,
    normalize = value => value
  } = options

  const pathModule = isWindows ? path.win32 : path.posix

  if (userDataOverride) {
    return pathModule.join(pathModule.resolve(userDataOverride), 'hermes-home')
  }

  const allowMigration = migrationAllowed(env)
  const explicit = env.QUORUM_HOME || (isWindows ? readWindowsUserEnvVar('QUORUM_HOME') : '')
  const stockRoots = [pathModule.join(homePath, '.hermes')]

  if (isWindows && localAppData) {
    stockRoots.push(pathModule.join(localAppData, 'hermes'))
  }

  if (explicit) {
    if (!allowMigration && stockRoots.some(root => samePath(explicit, root, pathModule))) {
      throw new Error(
        'QUORUM_HOME points at stock Hermes state. Set QUORUM_ALLOW_HERMES_HOME_MIGRATION=1 only for an intentional migration.'
      )
    }

    return normalize(explicit)
  }

  if (allowMigration) {
    const legacy = env.HERMES_HOME || (isWindows ? readWindowsUserEnvVar('HERMES_HOME') : '')

    if (legacy) {
      return normalize(legacy)
    }
  }

  if (isWindows && localAppData) {
    return pathModule.join(localAppData, PRODUCT_IDENTITY.windowsHomeDirName)
  }

  return pathModule.join(homePath, PRODUCT_IDENTITY.homeDirName)
}
