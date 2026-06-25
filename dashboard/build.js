// Build the deployable dashboard: inject Supabase config from env into dist/.
// The Supabase anon (publishable) key is public-safe by design, so the built
// artifact can carry it. The committed source keeps generic placeholders.
//
// Usage:  SUPABASE_URL=... SUPABASE_ANON_KEY=... node build.js
const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const url = process.env.SUPABASE_URL || "";
const key = process.env.SUPABASE_ANON_KEY || "";

const out = src
  .replace(/const SUPABASE_URL\s*=\s*"[^"]*"/, `const SUPABASE_URL = "${url}"`)
  .replace(/const SUPABASE_ANON_KEY\s*=\s*"[^"]*"/, `const SUPABASE_ANON_KEY = "${key}"`);

fs.mkdirSync(path.join(__dirname, "dist"), { recursive: true });
fs.writeFileSync(path.join(__dirname, "dist", "index.html"), out);
console.log(`built dist/index.html ${url ? "(supabase configured)" : "(NO supabase env — placeholders kept)"}`);

// Also publish the self-contained rehearsed demo flow at /demo/.
const demoSrc = path.join(__dirname, "..", "demo", "index.html");
if (fs.existsSync(demoSrc)) {
  fs.mkdirSync(path.join(__dirname, "dist", "demo"), { recursive: true });
  fs.copyFileSync(demoSrc, path.join(__dirname, "dist", "demo", "index.html"));
  console.log("copied demo -> dist/demo/index.html");
}
