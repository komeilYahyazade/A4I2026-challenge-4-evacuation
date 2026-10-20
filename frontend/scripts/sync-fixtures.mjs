// Copy contract/fixtures/*.json into src/fixtures/ so the Cloud Run build (context = frontend/)
// has them. Run after the agent lane republishes fixtures: npm run sync-fixtures
import { copyFileSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = join(here, "..", "..", "contract", "fixtures");
const dst = join(here, "..", "src", "fixtures");
mkdirSync(dst, { recursive: true });
// Synthetic fixtures only (the *.live.json captures are real data and not used as MOCK).
const KEEP = new Set(["allocation.sample.json", "county_map.lee.json", "readiness_brief.lee.json", "rehearsal.ian.sample.json", "shelter_verification.sample.json", "storm_track.ian.sample.json"]);
let n = 0;
for (const f of readdirSync(src)) {
  if (!KEEP.has(f)) continue;
  copyFileSync(join(src, f), join(dst, f));
  n++;
}
console.log(`synced ${n} fixtures from ${src}`);
