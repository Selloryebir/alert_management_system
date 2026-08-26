import { expect, test as base, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

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

    const passwordFile = process.env.E2E_ADMIN_PASSWORD_FILE;
    const requiresUiLogin = testInfo.file.endsWith("release-business.spec.ts");
    if (passwordFile && !requiresUiLogin) {
      const username = process.env.E2E_ADMIN_USERNAME ?? "admin";
      const password = readFileSync(passwordFile, "utf8").trim();
      expect(password, "E2E 管理员密码文件不能为空").not.toBe("");
      const csrfResponse = await page.request.get("/api/v1/auth/csrf");
      expect(csrfResponse.ok(), "E2E 登录前必须取得 CSRF 令牌").toBe(true);
      const csrf = (await csrfResponse.json()) as { header_name: string; token: string };
      const loginResponse = await page.request.post("/api/v1/auth/login", {
        data: { username, password },
        headers: { [csrf.header_name]: csrf.token },
      });
      expect(loginResponse.ok(), "E2E 管理员必须能够登录").toBe(true);
      await page.goto("/");
      await expect(page.getByText(`${username} · 系统管理员`, { exact: false })).toBeVisible();
    }

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
