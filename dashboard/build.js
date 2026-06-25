// Build the deployable dashboard: inject Supabase config (URL + publishable key)
// and publish /demo/. The dashboard talks to PostgREST directly via a fetch shim,
// so no supabase-js library is bundled (and no runtime CDN dependency).
//
// Usage:  SUPABASE_URL=... SUPABASE_ANON_KEY=... node build.js
const fs = require("fs");
const path = require("path");

let html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const url = process.env.SUPABASE_URL || "";
const key = process.env.SUPABASE_ANON_KEY || "";

html = html
  .replace(/const SUPABASE_URL\s*=\s*"[^"]*"/, `const SUPABASE_URL = "${url}"`)
  .replace(/const SUPABASE_ANON_KEY\s*=\s*"[^"]*"/, `const SUPABASE_ANON_KEY = "${key}"`);

fs.mkdirSync(path.join(__dirname, "dist"), { recursive: true });
fs.writeFileSync(path.join(__dirname, "dist", "index.html"), html);
console.log("built dist/index.html " + (url ? "(supabase configured)" : "(NO supabase env)"));

const demoSrc = path.join(__dirname, "..", "demo", "index.html");
if (fs.existsSync(demoSrc)) {
  fs.mkdirSync(path.join(__dirname, "dist", "demo"), { recursive: true });
  fs.copyFileSync(demoSrc, path.join(__dirname, "dist", "demo", "index.html"));
  console.log("copied demo -> dist/demo/index.html");
}
