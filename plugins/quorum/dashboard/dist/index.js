/* API-only dashboard registration. Quorum Desktop owns the full inspector UI. */
(() => {
  const plugins = window.__HERMES_PLUGINS__
  const sdk = window.__HERMES_PLUGIN_SDK__

  if (!plugins || !sdk) return

  plugins.register('quorum', () => null)
})()

