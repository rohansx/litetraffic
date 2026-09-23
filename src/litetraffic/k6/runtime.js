// LiteTraffic runtime helper. `verify` places this file at ./litetraffic/runtime.js
// inside a staged copy of the scenario; import it as "./litetraffic/runtime.js".
import crypto from "k6/crypto";
import exec from "k6/execution";

const MAX_DETAIL_CHARS = 500;

// k6 options for the frozen schedule: one ramping-arrival-rate scenario named
// "traffic" that runs the script's default export.
export function options() {
  const phases = JSON.parse(__ENV.LT_SCHEDULE_JSON);
  const maxVUs = Number(__ENV.LT_MAX_IN_FLIGHT);
  const stages = [];
  let rate = phases[0].rate;
  for (const phase of phases) {
    if (phase.rate !== rate) {
      stages.push({ target: phase.rate, duration: "0s" });
      rate = phase.rate;
    }
    // k6 excludes an admission exactly on the final duration boundary. The
    // extra millisecond makes an integer rate x duration plan deliver its final
    // journey without materially changing the requested rate.
    stages.push({ target: rate, duration: `${phase.seconds * 1000 + 1}ms` });
  }
  return {
    scenarios: {
      traffic: {
        executor: "ramping-arrival-rate",
        startRate: phases[0].rate,
        timeUnit: "1s",
        stages,
        preAllocatedVUs: maxVUs,
        maxVUs,
      },
    },
    maxRedirects: 0,
  };
}

// One key per journey: run id, k6 scenario and iteration, independent of the VU.
export function journeyKey() {
  return `${__ENV.LT_RUN_ID}-${exec.scenario.name}-${exec.scenario.iterationInTest}`;
}

// Log one assertion event. logicalKey defaults to journeyKey().
export function evidence(assertion, passed, { logicalKey, expected, actual, detail } = {}) {
  const event = {
    schema_version: 1,
    type: "assertion",
    run_id: __ENV.LT_RUN_ID,
    assertion,
    passed,
    logical_key: logicalKey === undefined ? journeyKey() : String(logicalKey),
  };
  if (expected !== undefined) event.expected = expected;
  if (actual !== undefined) event.actual = actual;
  if (detail !== undefined) event.detail = String(detail).slice(0, MAX_DETAIL_CHARS);
  console.log(`LT_EVENT ${JSON.stringify(event)}`);
}

// Deterministic [0, 1) generator seeded by LT_SEED and the iteration (mulberry32).
export function rng(iteration = exec.scenario.iterationInTest) {
  let state = 2166136261;
  for (const char of `${__ENV.LT_SEED || 0}:${iteration}`) {
    state = Math.imul(state ^ char.charCodeAt(0), 16777619);
  }
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

let pool;

// The fixture pool item for this journey (default: the k6 iteration index).
// Throws when LT_FIXTURE_POOL_JSON is unset or has no item at that index.
export function poolItem(index = exec.scenario.iterationInTest) {
  if (pool === undefined) {
    if (!__ENV.LT_FIXTURE_POOL_JSON) throw new Error("no fixture pool: set fixtures.pool or a setup 'pool' key");
    pool = JSON.parse(__ENV.LT_FIXTURE_POOL_JSON);
  }
  if (!Number.isInteger(index) || index < 0 || index >= pool.length) {
    throw new Error(`fixture pool has no item for journey ${index} (pool size ${pool.length})`);
  }
  return pool[index];
}

// HMAC-SHA256 of data under secret, for signing webhook bodies. Keep the secret
// in an env var the controller never records, e.g. __ENV.WEBHOOK_SECRET.
export function hmacSha256Hex(secret, data) {
  return crypto.hmac("sha256", secret, data, "hex");
}

export function hmacSha256Base64(secret, data) {
  return crypto.hmac("sha256", secret, data, "base64");
}

// Structural equality for JSON-like values: object key order is ignored, array
// order is not, and a key set to undefined differs from a missing key.
export function deepEqual(a, b) {
  if (a === b || (a !== a && b !== b)) return true; // NaN equals NaN
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  if (keysA.length !== keysB.length) return false;
  return keysA.every((key) => Object.prototype.hasOwnProperty.call(b, key) && deepEqual(a[key], b[key]));
}
