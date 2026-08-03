
import { pathToFileURL } from 'node:url';

// returns true if the passsed file is being invoked from node,
// not imported.
export function isMain(importMetaUrl) {
    const entrypoint = process.argv[1];
    return typeof entrypoint === 'string' && entrypoint.length > 0 && importMetaUrl === pathToFileURL(entrypoint).href;
}
