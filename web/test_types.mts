/**
 * Tests for priorityForBucket — the inverse of the priority_bucket rule.
 *
 *     npm test        (from web/)
 *
 * No test framework and no dependencies, matching scanner/test_extract.py.
 * Node strips the types; nothing is compiled or bundled.
 *
 * Why this file exists: priority_bucket is defined three times over — the
 * generated column in migrations/001_init.sql is the authority,
 * taskflow/models.py mirrors it for --dry-run, and priorityForBucket inverts
 * it so a card drag can write a urgency/impact pair instead of a bucket.
 * Twelve pairs collapse into four buckets, so the inverse is a judgement call
 * rather than a lookup, and nothing in the type system keeps it honest.
 *
 * FORWARD_RULE below is a hand-copy of the SQL. If a bucket rule ever changes,
 * this suite fails until it is copied again — which is the point.
 */

import {
  URGENCIES,
  IMPACTS,
  BUCKETS,
  priorityForBucket,
  type Bucket,
  type Urgency,
  type Impact,
} from "./lib/types.ts";

type Pair = { urgency: Urgency; impact: Impact };

/** Transcribed from the generated column in migrations/001_init.sql. */
function FORWARD_RULE({ urgency, impact }: Pair): Bucket {
  const urgent = urgency === "critical" || urgency === "high";
  const valuable = impact === "high";
  if (urgent && valuable) return "do_now";
  if (urgent) return "quick_win";
  if (valuable) return "schedule";
  return "backlog";
}

const ALL_PAIRS: Pair[] = URGENCIES.flatMap((urgency) =>
  IMPACTS.map((impact) => ({ urgency, impact })),
);

const ALL_BUCKETS: Bucket[] = BUCKETS.map((b) => b.key);

let passed = 0;
let failed = 0;

function check(label: string, cond: boolean, detail = ""): void {
  if (cond) {
    passed += 1;
    console.log(`  PASS  ${label}`);
  } else {
    failed += 1;
    console.log(`  FAIL  ${label}${detail ? ` — ${detail}` : ""}`);
  }
}

const show = (p: Pair) => `${p.urgency}/${p.impact}`;

/**
 * Sweep every pair against one bucket, returning the first violation as a
 * printable string. Exhaustive sweeps report one line rather than twelve —
 * a failure names the pair that broke it, which is the only part worth seeing.
 */
function sweep(
  bucket: Bucket,
  predicate: (before: Pair, after: Pair) => boolean,
): string {
  for (const before of ALL_PAIRS) {
    const after = priorityForBucket(bucket, before);
    if (!predicate(before, after)) {
      return `${show(before)} → ${show(after)}`;
    }
  }
  return "";
}

// ---------------------------------------------------------------------------
// The pairs and buckets themselves
// ---------------------------------------------------------------------------

console.log("\nGrid");
check("twelve urgency/impact pairs exist", ALL_PAIRS.length === 12, `got ${ALL_PAIRS.length}`);
check("four buckets exist", ALL_BUCKETS.length === 4, `got ${ALL_BUCKETS.length}`);
check(
  "every bucket is reachable from some pair",
  ALL_BUCKETS.every((b) => ALL_PAIRS.some((p) => FORWARD_RULE(p) === b)),
);

// ---------------------------------------------------------------------------
// Round trip — the property that actually matters for a drag
// ---------------------------------------------------------------------------

console.log("\nRound trip (a drop lands the card in the column it was dropped on)");
for (const bucket of ALL_BUCKETS) {
  const bad = sweep(bucket, (_, after) => FORWARD_RULE(after) === bucket);
  check(`every pair dropped on ${bucket} produces ${bucket}`, bad === "", bad);
}

// ---------------------------------------------------------------------------
// Idempotence
// ---------------------------------------------------------------------------

console.log("\nA pair already in the target bucket is left alone");
for (const bucket of ALL_BUCKETS) {
  const bad = sweep(
    bucket,
    (before, after) =>
      FORWARD_RULE(before) !== bucket ||
      (after.urgency === before.urgency && after.impact === before.impact),
  );
  check(`${bucket} pairs survive a drop on ${bucket} unchanged`, bad === "", bad);
}

// ---------------------------------------------------------------------------
// Minimal change — the documented reason this is not a lookup table
// ---------------------------------------------------------------------------

console.log("\nOnly the axis that disagrees with the target moves");
for (const bucket of ALL_BUCKETS) {
  const wantsUrgent = bucket === "do_now" || bucket === "quick_win";
  const bad = sweep(bucket, (before, after) => {
    const wasUrgent = before.urgency === "critical" || before.urgency === "high";
    // The urgency class already agrees, so the exact value must be preserved.
    return wasUrgent !== wantsUrgent || after.urgency === before.urgency;
  });
  check(`${bucket} keeps a compatible urgency verbatim`, bad === "", bad);
}

for (const bucket of ALL_BUCKETS) {
  const wantsHighImpact = bucket === "do_now" || bucket === "schedule";
  const bad = sweep(bucket, (before, after) =>
    before.impact === "high" || wantsHighImpact
      ? true
      : after.impact === before.impact,
  );
  check(`${bucket} keeps a compatible impact verbatim`, bad === "", bad);
}

console.log("\nWorked examples from the docs");
{
  const after = priorityForBucket("do_now", { urgency: "critical", impact: "low" });
  check(
    "critical/low dropped on Do now stays critical",
    after.urgency === "critical",
    after.urgency,
  );
  check("...and gains impact rather than flattening to high/high", after.impact === "high");
}
{
  const after = priorityForBucket("schedule", { urgency: "critical", impact: "high" });
  check(
    "critical/high dropped on Schedule loses urgency",
    FORWARD_RULE(after) === "schedule",
    show(after),
  );
  check("...and keeps its high impact", after.impact === "high", after.impact);
}
{
  const after = priorityForBucket("backlog", { urgency: "low", impact: "high" });
  check("low/high dropped on Backlog drops impact", after.impact !== "high", after.impact);
  check("...and leaves the already-patient urgency alone", after.urgency === "low", after.urgency);
}

// ---------------------------------------------------------------------------
// Output validity — these values go straight into Postgres enums
// ---------------------------------------------------------------------------

console.log("\nEvery output is a legal enum value");
{
  const urgencies = new Set<string>(URGENCIES);
  const impacts = new Set<string>(IMPACTS);
  const results = ALL_BUCKETS.flatMap((b) => ALL_PAIRS.map((p) => priorityForBucket(b, p)));
  check("all 48 results carry a known urgency", results.every((r) => urgencies.has(r.urgency)));
  check("all 48 results carry a known impact", results.every((r) => impacts.has(r.impact)));
  check("48 combinations were exercised", results.length === 48, `got ${results.length}`);
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
