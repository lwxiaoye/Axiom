/**
 * Main-chat capability entry visibility.
 *
 * These switches only control UI entry points. The Agent Harness keeps mounting
 * authorized connectors and maintaining the per-thread workspace in the backend.
 */
export const MAIN_CHAT_FEATURE_VISIBILITY = Object.freeze({
  connectors: false,
  workspace: false,
});
