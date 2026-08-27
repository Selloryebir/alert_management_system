import { expect, type Locator, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";

const repositoryRoot = path.resolve(__dirname, "../..");
const commit = execFileSync("git", ["rev-parse", "--short=12", "HEAD"], {
  cwd: repositoryRoot,
  encoding: "utf8",
}).trim();

const viewports = [
  { directory: "desktop-1440x1000", width: 1440, height: 1000 },
  { directory: "mobile-390x844", width: 390, height: 844 },
] as const;

export async function prepareVisualPage(page: Page): Promise<void> {
  await page.addStyleTag({ content: `
    *, *::before, *::after {
      animation-duration: 0s !important;
      animation-delay: 0s !important;
      transition-duration: 0s !important;
      caret-color: transparent !important;
    }
  ` });
}

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
      path: path.join(repositoryRoot, ".runtime", "ui-audit", commit, viewport.directory, `${name}.png`),
      fullPage: true,
      animations: "disabled",
      mask: masks,
      maskColor: "#687786",
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
}
