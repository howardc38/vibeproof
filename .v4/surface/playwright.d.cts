// Preserve the caller's Playwright/custom-fixture types without requiring that
// @playwright/test be resolvable from the framework directory in a monorepo.
export function surfaceConfig<T>(config: T): T;
export function surfaceTest<T>(base: T): T;
