// Attribute actual V8 function entries through a browser-observed source map.
// Node owns map decoding; source contents are hashed, never written to evidence.
const {SourceMap} = require('node:module');
const crypto = require('node:crypto');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
const LIMIT = 16 * 1024 * 1024;

function nameOffset(source, fn) {
  const start = fn.ranges?.[0]?.startOffset;
  if (!Number.isInteger(start) || !fn.functionName) return null;
  const head = source.slice(start, start + 512);
  const named = /^(?:async\s+)?function\s*\*?\s*([\w$]+)\s*\(/.exec(head);
  if (named && named[1] === fn.functionName) return start + named[0].lastIndexOf(named[1]);
  // V8 starts an arrow at its parameter list, after the binding's name.
  if (!/^(?:async\s*)?(?:[\w$]+|\([^)]*\))\s*=>/.test(head)) return null;
  const before = source.slice(Math.max(0, start - 256), start);
  const binding = /([\w$]+)\s*=\s*$/.exec(before);
  return binding && binding[1] === fn.functionName ? start - before.length + binding.index : null;
}

async function mapFunctions(script, parsed, request) {
  if (!parsed?.sourceMapURL) return null;
  try {
    let bytes, mapURL;
    if (parsed.sourceMapURL.startsWith('data:')) {
      const match = /^data:application\/json(?:;charset=[\w-]+)?;base64,([A-Za-z0-9+/=\s]+)$/.exec(parsed.sourceMapURL);
      if (!match || match[1].length > LIMIT * 2) return null;
      bytes = Buffer.from(match[1], 'base64'); mapURL = 'inline';
    } else {
      const url = new URL(parsed.sourceMapURL, script.url), served = new URL(script.url);
      if (!['http:', 'https:'].includes(url.protocol) || url.origin !== served.origin || url.username || url.password) return null;
      const response = await request.get(url.href, {maxRedirects:0, timeout:5000});
      try {
        if (response.status() !== 200 || Number(response.headers()['content-length']) > LIMIT) return null;
        bytes = await response.body(); mapURL = url.origin + url.pathname;
      } finally { await response.dispose(); }
    }
    if (bytes.length > LIMIT) return null;
    const payload = JSON.parse(bytes.toString('utf8'));
    if (payload.version !== 3 || payload.sections || typeof payload.mappings !== 'string' ||
        !Array.isArray(payload.sources) || !Array.isArray(payload.sourcesContent) ||
        payload.sources.length !== payload.sourcesContent.length) return null;
    // Source names are untrusted metadata, not paths to fetch or read. Stable
    // indexes let the maintained decoder select the corresponding content.
    const map = new SourceMap({...payload, sourceRoot:'', sources:payload.sources.map((_, i) => String(i))});
    const functions = [];
    for (const fn of script.functions || []) {
      const offset = nameOffset(script.source, fn);
      if (offset === null) continue;
      const prefix = script.source.slice(0, offset), line = prefix.split('\n').length - 1;
      const column = offset - prefix.lastIndexOf('\n') - 1;
      const entry = map.findEntry(line, column);
      // A nearest preceding segment could point at another function or module.
      if (entry.generatedLine !== line || entry.generatedColumn !== column) continue;
      const original = payload.sourcesContent[Number(entry.originalSource)];
      if (typeof original !== 'string') continue;
      functions.push({source_sha256:sha(original), original_line:entry.originalLine,
        original_column:entry.originalColumn, count:fn.ranges[0].count,
        generated_line:line, generated_column:column});
    }
    return {sha256:sha(bytes), url:mapURL, functions};
  } catch { return null; } // Missing/unreadable mapping remains unproved.
}

module.exports = {mapFunctions};
