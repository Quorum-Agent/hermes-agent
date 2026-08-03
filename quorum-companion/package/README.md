# Quorum Companion for Hermes

Quorum Companion adds Quorum compatibility status and a process-local
inspection surface to compatible Hermes installations. It can store Quorum
policy preferences for migration/compatibility, but stock Hermes does **not**
enforce those preferences. Companion does **not** turn stock Hermes into the
fail-closed Quorum Edition: third-party plugins, auxiliary model paths, and
unsupported transports may bypass best-effort plugin hooks.

## Install

1. Extract the archive.
2. Stop the Hermes desktop/backend process.
3. From the extracted `quorum-plugin` directory, run:

   ```bash
   python install.py
   hermes plugins enable quorum
   ```

4. Restart Hermes Desktop. The Python API is mounted only during backend
   startup, so a restart is required even if the desktop plugin appears early.

Use `python install.py --hermes-home /path/to/profile/home` for a non-default
profile. Re-running against an existing installation refuses to overwrite it;
use `--replace` for a transactional update.

## Security semantics

- Inspection events live only in process memory and are not an audit ledger.
- The settings surface never exposes an enforcement on/off switch.
- When no Quorum Edition host guard is present, the Companion UI says so.
- Install only archives whose hashes and release provenance you trust.
