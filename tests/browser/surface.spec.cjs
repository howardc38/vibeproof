const {test: base, expect} = require('@playwright/test');
const {surfaceTest} = require('./.v4/surface/playwright.cjs');
const test = surfaceTest(base);
const mode = process.env.PROBE_MODE;
test('save caption', {annotation: {type:'v4-case', description:'caption-save'}}, async ({page, request}) => {
  test.skip(mode === 'skip', 'negative control: required UI flow skipped');
  if (mode === 'api-only') {
    expect((await request.get('/state')).ok()).toBe(true);
    return;
  }
  if (mode === 'expected-failure') test.fail();
  await page.goto('/');
  await page.getByLabel('Caption').fill('surface readback');
  await page.getByRole('button', {name:'Save'}).click();
  await expect(page.getByRole('status')).toHaveText('Saved');
  // Ask the state owner after the UI action; the POST's own ok is insufficient.
  expect(await (await request.get('/state')).json()).toEqual({caption:'surface readback'});
  if (mode === 'expected-failure') expect(1).toBe(2);
});
base('extra arithmetic control', async () => { expect(2 + 2).toBe(4); });
