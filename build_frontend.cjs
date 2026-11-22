"use strict";

// Keep the single-file deployment fallback in sync with the canonical UI assets.
const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");
const assert = require("node:assert/strict");

const root = path.resolve(__dirname, "..");
const names = ["index.html", "app.js", "styles.css"];
const assets = Object.fromEntries(names.map(name => [name, fs.readFileSync(path.join(root, "webapp/static", name), "utf8")]));
const encoded = zlib.deflateSync(Buffer.from(JSON.stringify(assets), "utf8"), { level: 9 }).toString("base64");
const mainPath = path.join(root, "webapp/main.py");
const source = fs.readFileSync(mainPath, "utf8");
const pattern = /(# BEGIN FRONTEND BUNDLE[^\r\n]*\r?\n)[\s\S]*?(# END FRONTEND BUNDLE)/;
assert.ok(pattern.test(source), "Missing embedded frontend markers");
const chunks = encoded.match(/.{1,100}/g);
const replacement = "_FRONTEND_BUNDLE = (\n" + chunks.map(chunk => '    "' + chunk + '"').join("\n") + "\n)\n";
const updated = source.replace(pattern, (_, begin, end) => begin + replacement + end);
fs.writeFileSync(mainPath, updated, "utf8");
assert.deepEqual(JSON.parse(zlib.inflateSync(Buffer.from(encoded, "base64")).toString("utf8")), assets);
console.log("Embedded frontend built and checked: " + names.join(", ") + ".");
