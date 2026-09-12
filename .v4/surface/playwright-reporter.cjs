const fs = require('node:fs');
const {managedServers} = require('./playwright.cjs');

class SurfaceReporter {
  constructor() { this.errors = []; this.results = new Map(); this.tests = []; }
  onBegin(config, suite) {
    this.config = config;
    this.tests = suite.allTests();
    this.ids = new Map();
    for (const test of this.tests) {
      const annotations = test.annotations.filter(a => a.type === 'v4-case');
      if (annotations.length > 1) this.errors.push(`multiple v4-case IDs: ${test.title}`);
      const id = annotations[0]?.description || test.id;
      if ([...this.ids.values()].includes(id)) this.errors.push(`duplicate surface case: ${id}`);
      this.ids.set(test.id, id);
    }
    if (config.updateSnapshots !== 'none' || config.projects.some(p => p.retries !== 0))
      this.errors.push('proof cannot update snapshots or enable retries');
  }
  onTestEnd(test, result) {
    // Unexpected passes, expected failures and retries are not fresh PASS proof.
    const status = result.status === 'skipped' ? 'skipped' :
      result.status === 'passed' && test.expectedStatus === 'passed' && result.retry === 0
        ? 'passed' : 'failed';
    const row = { id: this.ids.get(test.id), status };
    const attachment = result.attachments.find(a => a.name === 'v4-browser');
    if (attachment) {
      try {
        row.browser = JSON.parse(attachment.body || fs.readFileSync(attachment.path));
      } catch (error) { this.errors.push(`cannot read browser observation: ${error.message}`); }
    }
    const previous = this.results.get(test.id);
    if (previous?.status === 'failed') row.status = 'failed';
    this.results.set(test.id, row);
  }
  onError(error) { this.errors.push(error.message || String(error)); }
  onEnd(result) {
    if (!process.env.V4_SURFACE_RESULT) return;
    if (result.status !== 'passed') this.errors.push(`runner ended ${result.status}`);
    const managed_servers = managedServers(this.config);
    const receipt = { schema: 1, run_id: process.env.V4_SURFACE_RUN_ID,
      kind: 'browser', cwd: fs.realpathSync(process.cwd()), managed_servers,
      planned: [...(this.ids?.values() || [])],
      checks: this.tests.map(test => this.results.get(test.id) || {
        id: this.ids.get(test.id), status: 'not_run' }), errors: this.errors };
    fs.writeFileSync(process.env.V4_SURFACE_RESULT, JSON.stringify(receipt, null, 2));
  }
}
module.exports = SurfaceReporter;
