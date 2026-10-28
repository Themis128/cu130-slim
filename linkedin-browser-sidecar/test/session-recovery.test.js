import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createCrashTracker,
  pageNeedsRecovery,
  resolveRecoveryState,
} from "../session-recovery.js";

const fakePage = (closed = false) => ({ isClosed: () => closed });
const st = (v) => ({ cookies: [{ name: "li_at", value: v }], origins: [] });

test("crash in a background tab does not flag the active page", () => {
  const t = createCrashTracker();
  const active = fakePage();
  const bg = fakePage();
  t.mark(bg);
  assert.equal(pageNeedsRecovery(active, t), false);
  assert.equal(pageNeedsRecovery(bg, t), true);
});

test("wiring a new tab does not clear an existing crash", () => {
  const t = createCrashTracker();
  const crashed = fakePage();
  t.mark(crashed);
  const fresh = fakePage();
  assert.equal(t.isCrashed(fresh), false);
  assert.equal(t.isCrashed(crashed), true);
});

test("missing, closed or throwing pages need recovery", () => {
  const t = createCrashTracker();
  assert.equal(pageNeedsRecovery(null, t), true);
  assert.equal(pageNeedsRecovery(fakePage(true), t), true);
  assert.equal(pageNeedsRecovery({ isClosed: () => { throw new Error("x"); } }, t), true);
});

test("recovery prefers live context cookies over saved and startup", async () => {
  const r = await resolveRecoveryState({
    context: { storageState: async () => st("live") },
    readSaved: () => st("saved"),
    current: st("startup"),
  });
  assert.equal(r.source, "live");
  assert.equal(r.state.cookies[0].value, "live");
});

test("falls back to the saved session file when the context is dead", async () => {
  const r = await resolveRecoveryState({
    context: { storageState: async () => { throw new Error("Target closed"); } },
    readSaved: () => st("saved"),
    current: st("startup"),
  });
  assert.equal(r.source, "saved");
  assert.equal(r.state.cookies[0].value, "saved");
});

test("empty live state falls through to saved", async () => {
  const r = await resolveRecoveryState({
    context: { storageState: async () => ({ cookies: [], origins: [] }) },
    readSaved: () => st("saved"),
    current: st("startup"),
  });
  assert.equal(r.source, "saved");
});

test("uses in-memory state only as last resort", async () => {
  const r = await resolveRecoveryState({ context: null, readSaved: () => null, current: st("startup") });
  assert.equal(r.source, "memory");
  assert.equal(r.state.cookies[0].value, "startup");
  const none = await resolveRecoveryState({ context: null, readSaved: null, current: undefined });
  assert.equal(none.state, null);
});
