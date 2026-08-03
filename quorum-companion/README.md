# Quorum Companion packaging

This directory builds `quorum-plugin.zip` for existing stock-Hermes users.
The archive contains the real Python plugin/dashboard API plus a runtime
desktop status surface and a transactional installer.

Quorum Companion is intentionally not described as a fail-closed security
boundary. Only Quorum Edition owns the mandatory dispatch guard. On stock
Hermes, Companion provides status, configuration, and compatibility visibility
where the host exposes them.

Build with:

```bash
python quorum-companion/build.py --output dist/quorum-plugin.zip
```

The ZIP is reproducible: entries are sorted, timestamps and permissions are
fixed, and `SHA256SUMS` covers every payload file.

