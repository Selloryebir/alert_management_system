import { expect, type Locator, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";

const repositoryRoot = path.resolve(__dirname, "../..");
const providedCommit = process.env.E2E_SOURCE_COMMIT?.trim();
if (providedCommit && !/^[0-9a-f]{40}$/i.test(providedCommit)) {
  throw new Error("E2E_SOURCE_COMMIT 必须是 40 位 Git SHA");
}
const commit = providedCommit?.slice(0, 12)
  || execFileSync("git", ["rev-parse", "--short=12", "HEAD"], {
    cwd: repositoryRoot,
    encoding: "utf8",
  }).trim();
const outputRoot = process.env.E2E_VISUAL_OUTPUT_DIR
  ? path.resolve(process.env.E2E_VISUAL_OUTPUT_DIR)
  : path.join(repositoryRoot, ".runtime", "ui-audit");

const viewports = [
  { directory: "desktop-1440x1000", width: 1440, height: 1000 },
  { directory: "mobile-390x844", width: 390, height: 844 },
] as const;

export async function assertNoDocumentOverflow(page: Page): Promise<void> {
  const overflow = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(overflow.scrollWidth, `页面不得产生全局水平滚动：${JSON.stringify(overflow)}`)
    .toBeLessThanOrEqual(overflow.clientWidth + 1);
}

export async function captureVisualState(
  page: Page,
  name: string,
  focus?: Locator,
): Promise<void> {
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await assertNoDocumentOverflow(page);
    if (focus) await focus.scrollIntoViewIfNeeded();
    const masks = [
      page.locator('input[type="password"]'),
      page.getByRole("status").filter({ hasText: /分析运行 [0-9a-f-]{36}/ }),
      page.locator(".recent-batches tbody td:last-child"),
      page.locator(".project-current .table-wrap tbody td:last-child"),
      page.locator('[data-testid="recovery-point-table"] tbody td:nth-child(2)'),
      page.locator('[data-testid="disposition-history"] tbody td:first-child'),
      page.locator('[data-testid="audit-table"] tbody td:nth-child(1)'),
      page.locator('[data-testid="audit-table"] tbody td:nth-child(4)'),
    ];
    await page.screenshot({
      path: path.join(outputRoot, commit, viewport.directory, `${name}.png`),
      fullPage: true,
      animations: "disabled",
      mask: masks,
      maskColor: "#687786",
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
}
