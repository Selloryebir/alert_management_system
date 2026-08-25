import { expect, test as base, type Page } from "@playwright/test";

type BrowserErrorFixtures = {
  browserErrors: string[];
};

export const test = base.extend<BrowserErrorFixtures>({
  browserErrors: [async ({ page }, use, testInfo) => {
    const browserErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") {
        browserErrors.push(`console.error: ${message.text()}`);
      }
    });
    page.on("pageerror", (error) => {
      browserErrors.push(`pageerror: ${error.stack ?? error.message}`);
    });

    await use(browserErrors);

    if (browserErrors.length > 0) {
      await testInfo.attach("browser-errors.json", {
        body: Buffer.from(`${JSON.stringify(browserErrors, null, 2)}\n`, "utf8"),
        contentType: "application/json",
      });
    }
    expect(browserErrors, "浏览器不得出现 console.error 或 pageerror").toEqual([]);
  }, { auto: true }],
});

export async function injectNextFetchFailure(
  page: Page,
  pathSuffix: string,
  body: Record<string, string>,
): Promise<void> {
  await page.evaluate(({ suffix, responseBody }) => {
    const originalFetch = window.fetch;
    window.fetch = async (...args) => {
      const input = args[0];
      const url = input instanceof Request ? input.url : String(input);
      if (url.endsWith(suffix)) {
        window.fetch = originalFetch;
        return new Response(JSON.stringify(responseBody), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        });
      }
      return originalFetch(...args);
    };
  }, { suffix: pathSuffix, responseBody: body });
}
