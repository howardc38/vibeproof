// Optional Playwright integration. It imports no runner: use the adopter's own
// @playwright/test instance, including its custom fixtures and monorepo layout.
const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const {mapFunctions} = require('./playwright-sourcemap.cjs');

function managedServers(config) {
  const configDir = path.dirname(config?.configFile || path.join(process.cwd(), 'playwright.config.ts'));
  return (config?.metadata.v4SurfaceServers || []).map(server => ({
    cwd: fs.realpathSync(path.resolve(configDir, server.cwd)), reuse: server.reuse,
  }));
}

function safeURL(value) {
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) ? url.origin + url.pathname : '';
  } catch { return ''; }
}

function surfaceConfig(config) {
  if (!process.env.V4_SURFACE_RUN_ID && !process.env.V4_BROWSER_TRACE_DIR) return config;
  const original = config.webServer ? [].concat(config.webServer) : [];
  if (!original.length) throw new Error('V4 browser proof needs a local webServer command');
  const webServer = original.map(server => ({ ...server, reuseExistingServer: false }));
  const reporter = config.reporter === undefined ? [['list']] :
    typeof config.reporter === 'string' ? [[config.reporter]] : config.reporter;
  const output = process.env.V4_SURFACE_RESULT ? path.dirname(process.env.V4_SURFACE_RESULT)
    : process.env.V4_BROWSER_TRACE_DIR;
  return {
    ...config,
    webServer: Array.isArray(config.webServer) ? webServer : webServer[0],
    metadata: { ...config.metadata, v4SurfaceServers: webServer.map(server => ({
      cwd: server.cwd || '.', reuse: server.reuseExistingServer,
    })) },
    reporter: [...reporter, [path.join(__dirname, 'playwright-reporter.cjs')]],
    outputDir: path.join(output, 'artifacts'),
    forbidOnly: true, updateSnapshots: 'none', retries: 0,
    projects: config.projects?.map((project, index) => ({ ...project, retries: 0,
      outputDir: path.join(output, 'artifacts', String(index)) })),
  };
}

function surfaceTest(base) {
  return base.extend({
    v4SurfaceObservation: [async ({ page, browserName }, use, testInfo) => {
      const run_id = process.env.V4_SURFACE_RUN_ID;
      const traceDirectory = process.env.V4_BROWSER_TRACE_DIR;
      if (!run_id && !traceDirectory) return use();
      const urls = new Set();
      const observe = frame => {
        if (frame !== page.mainFrame()) return;
        const url = safeURL(frame.url());
        if (url) urls.add(url);
      };
      page.on('framenavigated', observe);
      const trace = traceDirectory && browserName === 'chromium';
      let debuggerSession;
      const parsedScripts = new Map();
      if (trace) {
        debuggerSession = await page.context().newCDPSession(page);
        debuggerSession.on('Debugger.scriptParsed', entry => parsedScripts.set(entry.scriptId, entry));
        await debuggerSession.send('Debugger.enable');
        await page.coverage.startJSCoverage({resetOnNavigation:false});
      }
      try { await use(); }
      finally {
        page.off('framenavigated', observe);
        if (run_id) await testInfo.attach('v4-browser', { contentType: 'application/json',
          body: Buffer.from(JSON.stringify({ run_id, name: browserName, urls: [...urls] })) });
        if (trace) {
          const scripts = [];
          // A page controls its script count. Fetch/decode maps one at a time
          // so a large page cannot start an unbounded batch of map requests.
          for (const script of await page.coverage.stopJSCoverage()) {
            if (typeof script.source !== 'string' || !safeURL(script.url)) continue;
            scripts.push({url:safeURL(script.url),
              source_sha256:crypto.createHash('sha256').update(script.source, 'utf8').digest('hex'),
              functions:script.functions,
              source_map:await mapFunctions(script, parsedScripts.get(script.scriptId), page.request)});
          }
          await debuggerSession.detach();
          // Preserve browser provenance. Never disguise an HTTP script as a
          // Node file URL, or persist source text/credential-bearing query data.
          const record = {schema:1,run_id:process.env.V4_BROWSER_TRACE_RUN,
            repo:fs.realpathSync(process.env.V4_BROWSER_TRACE_ROOT),cwd:fs.realpathSync(process.cwd()),browser:browserName,
            update_snapshots:testInfo.config.updateSnapshots,
            retries:testInfo.project.retries,retry:testInfo.retry,
            expected_status:testInfo.expectedStatus,status:testInfo.status,
            managed_servers:managedServers(testInfo.config),scripts};
          fs.writeFileSync(path.join(traceDirectory, crypto.randomUUID()+'.json'), JSON.stringify(record));
        }
      }
    }, { auto: true }],
  });
}

module.exports = { surfaceConfig, surfaceTest, managedServers };
