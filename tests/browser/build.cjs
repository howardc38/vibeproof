// Test-only compiler and intentionally broken map controls; never an adopter default.
const esbuild = require('esbuild');
const fs = require('node:fs');
const mode = process.env.PROBE_MAP || 'linked';
esbuild.buildSync({entryPoints:['client.ts'],outfile:'bundle/client.js',bundle:true,
  minify:true,sourcemap:mode === 'inline' ? 'inline' : true});
if (mode === 'missing') fs.unlinkSync('bundle/client.js.map');
if (['stale','no-content'].includes(mode)) {
  const map=JSON.parse(fs.readFileSync('bundle/client.js.map','utf8'));
  if (mode === 'stale') map.sourcesContent=map.sourcesContent.map(source => '// stale\n'+source);
  else delete map.sourcesContent;
  fs.writeFileSync('bundle/client.js.map',JSON.stringify(map));
}
if (mode === 'cross-origin') {
  const code=fs.readFileSync('bundle/client.js','utf8');
  fs.writeFileSync('bundle/client.js',code.replace('sourceMappingURL=client.js.map',
    'sourceMappingURL=http://127.0.0.1:1/do-not-fetch.map'));
}
