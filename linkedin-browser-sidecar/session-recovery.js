// Helpers for recovering a crashed/closed Playwright page without losing the
// LinkedIn session. Kept free of patchright/express so they unit-test cleanly.

/**
 * Per-page crash tracking. A single shared boolean is wrong: a crash in a
 * background tab would wipe a healthy active page, and wiring a new tab
 * would clear the flag for a page that really did crash.
 */
export function createCrashTracker() {
  const crashed = new WeakSet();
  return {
    mark(p) {
      if (p) crashed.add(p);
    },
    isCrashed(p) {
      return !!p && crashed.has(p);
    },
  };
}

/** True when the active page can't serve requests and the context must be rebuilt. */
export function pageNeedsRecovery(page, tracker) {
  if (!page) return true;
  try {
    if (page.isClosed()) return true;
  } catch (_) {
    return true;
  }
  return tracker.isCrashed(page);
}

function hasCookies(state) {
  return !!state && Array.isArray(state.cookies) && state.cookies.length > 0;
}

/**
 * Pick the freshest session to rebuild with: the live context's state (the
 * context usually survives a page crash), else the last saved session file,
 * else whatever is in memory (possibly the startup cookies).
 */
export async function resolveRecoveryState({ context, readSaved, current }) {
  if (context) {
    try {
      const live = await context.storageState();
      if (hasCookies(live)) return { state: live, source: "live" };
    } catch (_) {}
  }
  if (readSaved) {
    try {
      const saved = await readSaved();
      if (hasCookies(saved)) return { state: saved, source: "saved" };
    } catch (_) {}
  }
  return { state: current ?? null, source: "memory" };
}
