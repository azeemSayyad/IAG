import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, dirname, extname } from "node:path";

const root = "apps/frontendall";
const textExts = new Set([".html", ".js", ".css"]);
const assetAttrs = ["src", "href"];
const errors = [];

function walk(dir) {
  const entries = readdirSync(dir);
  const files = [];
  for (const entry of entries) {
    const path = join(dir, entry);
    const stat = statSync(path);
    if (stat.isDirectory()) files.push(...walk(path));
    else files.push(path);
  }
  return files;
}

function isLocalAsset(value) {
  return (
    value &&
    !value.startsWith("http://") &&
    !value.startsWith("https://") &&
    !value.startsWith("//") &&
    !value.startsWith("#") &&
    !value.startsWith("mailto:") &&
    !value.startsWith("tel:") &&
    !value.startsWith("data:")
  );
}

for (const file of walk(root)) {
  if (!textExts.has(extname(file).toLowerCase())) continue;

  const text = readFileSync(file, "utf8");
  if (/[âÂ]/.test(text)) {
    errors.push(`${file}: contains mojibake marker`);
  }

  if (file.endsWith(".html")) {
    if (!/<meta\s+charset=["']?UTF-8["']?\s*\/?>/i.test(text)) {
      errors.push(`${file}: missing UTF-8 charset meta`);
    }

    for (const attr of assetAttrs) {
      const pattern = new RegExp(`${attr}=["']([^"']+)["']`, "gi");
      for (const match of text.matchAll(pattern)) {
        // Strip both the query string and the #fragment — neither is part of the
        // file path on disk (e.g. "settings.html#npn-licenses" is the file
        // settings.html plus an in-page anchor, not a file literally named that).
        const raw = match[1].split("?")[0].split("#")[0];
        if (!isLocalAsset(raw)) continue;
        // A leading "/" is the deployed SITE root, which is apps/frontendall —
        // not the directory the referencing file happens to live in. (e.g. the
        // SMS SPA at /sms/ loads the shared /brand.js from the root.)
        const target = raw.startsWith("/")
          ? join(root, raw.slice(1))
          : join(dirname(file), raw);
        if (!existsSync(target)) {
          errors.push(`${file}: missing local asset ${match[1]}`);
        }
      }
    }
  }
}

if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}

console.log("Frontend static validation passed.");
