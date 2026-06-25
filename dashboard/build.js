// Build the deployable dashboard: inject Supabase config + INLINE supabase-js
// (so the page needs no CDN at runtime — venue-wifi safe), and publish /demo/.
//
// Usage:  SUPABASE_URL=... SUPABASE_ANON_KEY=... node build.js
const fs = require("fs");
const path = require("path");
const https = require("https");

function fetchText(url) {
  return new Promise((resolve, reject) => {
    https
      .get(url, { headers: { "User-Agent": "incline-build" } }, (r) => {
        if (r.statusCode >= 300 && r.statusCode < 400 && r.headers.location) {
          return fetchText(r.headers.location).then(resolve, reject);
        }
        if (r.statusCode !== 200) return reject(new Error("status " + r.statusCode));
        let d = "";
        r.on("data", (c) => (d += c));
        r.on("end", () => resolve(d));
      })
      .on("error", reject);
  });
}

(async () => {
  let html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
  const url = process.env.SUPABASE_URL || "";
  const key = process.env.SUPABASE_ANON_KEY || "";

  html = html
    .replace(/const SUPABASE_URL\s*=\s*"[^"]*"/, `const SUPABASE_URL = "${url}"`)
    .replace(/const SUPABASE_ANON_KEY\s*=\s*"[^"]*"/, `const SUPABASE_ANON_KEY = "${key}"`);

  // Inline the supabase-js UMD bundle so there is no runtime CDN dependency.
  try {
    const lib = await fetchText(
      "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.js"
    );
    if (lib && lib.includes("createClient")) {
      html = html.replace(
        /<script src="https:\/\/cdn\.jsdelivr\.net\/npm\/@supabase\/supabase-js@2"><\/script>/,
        "<script>\n" + lib + "\n</script>"
      );
      console.log("inlined supabase-js (" + Math.round(lib.length / 1024) + " KB)");
    } else {
      console.log("supabase-js fetch unexpected; keeping CDN tag");
    }
  } catch (e) {
    console.log("supabase-js inline failed, keeping CDN tag:", e.message);
  }

  fs.mkdirSync(path.join(__dirname, "dist"), { recursive: true });
  fs.writeFileSync(path.join(__dirname, "dist", "index.html"), html);
  console.log(
    "built dist/index.html " + (url ? "(supabase configured)" : "(NO supabase env)")
  );

  const demoSrc = path.join(__dirname, "..", "demo", "index.html");
  if (fs.existsSync(demoSrc)) {
    fs.mkdirSync(path.join(__dirname, "dist", "demo"), { recursive: true });
    fs.copyFileSync(demoSrc, path.join(__dirname, "dist", "demo", "index.html"));
    console.log("copied demo -> dist/demo/index.html");
  }
})();
