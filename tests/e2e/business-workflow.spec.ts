import { expect, type Page, type APIResponse, type Response } from "@playwright/test";
import path from "node:path";
import { injectNextFetchFailure, test } from "./test-fixtures";

const repositoryRoot = path.resolve(__dirname, "../..");
const mode = process.env.E2E_MODE ?? "smoke";
const expectedTotal = Number(process.env.E2E_EXPECTED_TOTAL ?? "300");
const dataset = path.resolve(
  repositoryRoot,
  process.env.E2E_DATASET ?? "samples/smoke/synthetic_smoke_utf8.csv",
);

type PreviewResponse = { batch_id: string; total_rows: number; status: string };
type DashboardResponse = {
  run_id: string;
  total: number;
  noise_type_counts: Record<string, number>;
  cause_category_counts: Record<string, number>;
};

async function responseJson<T>(response: APIResponse | Response): Promise<T> {
  const body = await response.text();
  expect(response.ok(), body).toBeTruthy();
  return JSON.parse(body) as T;
}

async function importAndAnalyze(page: Page): Promise<{ batchId: string; runId: string }> {
  await page.getByTestId("select-project-DEFAULT-DEMO").click();
  await expect(page.getByTestId("file-input")).toBeEnabled();
  await page.getByTestId("file-input").setInputFiles(dataset);
  const previewResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/imports/preview") && response.request().method() === "POST",
  );
  await page.getByTestId("preview-button").click();
  const preview = await responseJson<PreviewResponse>(await previewResponse);
  expect(preview.status).toBe("READY");
  expect(preview.total_rows).toBe(expectedTotal);
  await expect(page.getByTestId("preview-summary")).toContainText(String(expectedTotal));

  const confirmResponse = page.waitForResponse(
    (response) => response.url().includes(`/api/v1/imports/${preview.batch_id}/confirm`),
  );
  await page.getByTestId("confirm-import").click();
  const confirmed = await responseJson<{ status: string }>(await confirmResponse);
  expect(confirmed.status).toBe("IMPORTED");

  const analysisResponse = page.waitForResponse(
    (response) => response.url().includes(`/api/v1/imports/${preview.batch_id}/analyses`)
      && response.request().method() === "POST",
  );
  await page.getByTestId("start-analysis").click();
  const response = await analysisResponse;
  expect(response.ok()).toBeTruthy();
  const status = page.getByRole("status").filter({ hasText: "分析运行" });
  await expect(status).toContainText(/分析运行 [0-9a-f-]{36} 已加载/);
  const statusText = (await status.textContent()) ?? "";
  const runId = statusText.match(/[0-9a-f-]{36}/)?.[0];
  expect(runId).toBeTruthy();

  await expect(page.getByTestId("dashboard-total")).toContainText(String(expectedTotal));
  return { batchId: preview.batch_id, runId: runId! };
}

test("浏览器完成导入、分析、详情、事件链和人工处置闭环", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "报警管理系统" })).toBeVisible();

  const { runId } = await importAndAnalyze(page);
  const dashboardResponse = await page.request.get(`/api/v1/analyses/${runId}/dashboard`);
  const dashboard = await responseJson<DashboardResponse>(dashboardResponse);
  expect(dashboard.run_id).toBe(runId);
  expect(dashboard.total).toBe(expectedTotal);

  if (mode === "demo") {
    return;
  }

  expect(dashboard.noise_type_counts).toEqual({
    NORMAL: 170,
    DUPLICATE: 30,
    CHATTER: 40,
    SHORT_LIVED: 30,
    PERSISTENT: 30,
  });
  expect(dashboard.cause_category_counts).toEqual({
    PROCESS_DISTURBANCE: 30,
    EQUIPMENT_FAULT: 30,
    INSTRUMENT_ISSUE: 30,
    MAINTENANCE_TEST: 20,
    UNKNOWN: 190,
  });
  await expect(page.getByTestId("dashboard-chains")).toContainText("12");
  for (const [name, value] of Object.entries(dashboard.noise_type_counts)) {
    await expect(page.getByTestId(`dashboard-noise-${name}`)).toContainText(String(value));
  }
  for (const [name, value] of Object.entries(dashboard.cause_category_counts)) {
    await expect(page.getByTestId(`dashboard-cause-${name}`)).toContainText(String(value));
  }

  await page.getByTestId("filter-noise").selectOption("PERSISTENT");
  await page.getByTestId("filter-cause").selectOption("MAINTENANCE_TEST");
  await page.getByRole("button", { name: "应用筛选" }).click();
  await expect(page.getByTestId("empty-state")).toBeVisible();

  await page.getByTestId("filter-noise").selectOption("");
  await page.getByTestId("filter-cause").selectOption("EQUIPMENT_FAULT");
  await page.getByRole("button", { name: "应用筛选" }).click();
  await page.getByTestId("alarm-row-222").click();
  await expect(page.getByTestId("alarm-detail")).toBeVisible();
  await expect(page.getByTestId("detail-source-row")).toContainText("222");
  await expect(page.getByTestId("detail-evidence")).not.toBeEmpty();
  await expect(page.getByTestId("detail-event-chains")).toContainText("不代表已确认根因");
  await expect(page.getByTestId("event-chain")).toContainText("222 → 223 → 224 → 225 → 226");

  const operator = "admin";
  const assignee = "admin";
  const startedNote = "[SYNTHETIC] E2E 开始处置";
  const closedNote = "[SYNTHETIC] E2E 审核完成";
  await page.getByTestId("disposition-assignee").fill(assignee);
  await page.getByTestId("disposition-note").fill(startedNote);
  await page.getByTestId("disposition-start").click();
  await expect(page.getByTestId("disposition-history")).toContainText("待处理 → 处理中");
  await expect(page.getByTestId("disposition-history")).toContainText(assignee);
  await expect(page.getByTestId("disposition-history")).toContainText(startedNote);

  await page.getByTestId("disposition-note").fill(closedNote);
  await page.getByTestId("disposition-close").click();
  const history = page.getByTestId("disposition-history");
  await expect(history).toContainText("处理中 → 已关闭");
  await expect(history).toContainText(operator);
  await expect(history).toContainText(closedNote);
  await expect(history).toContainText(/20\d{2}[-/]\d{2}[-/]\d{2}/);

  const detailResponse = await page.request.get(`/api/v1/analyses/${runId}/alarms?page=0&size=1&cause_category=EQUIPMENT_FAULT`);
  const pagePayload = await responseJson<{ total: number; items: Array<{ source_row: number }> }>(detailResponse);
  expect(pagePayload.total).toBe(30);
  expect(pagePayload.items[0].source_row).toBe(222);
});

test("页面显示算法不可用的可重试提示", async ({ page }) => {
  test.skip(mode === "demo", "20,000 行冒烟只执行首屏主路径");
  await page.goto("/");
  await injectNextFetchFailure(page, "/analyses", {
    code: "ALGORITHM_UNAVAILABLE",
    message: "算法服务不可用，可重试",
  });

  await page.getByTestId("select-project-DEFAULT-DEMO").click();
  await expect(page.getByTestId("file-input")).toBeEnabled();
  await page.getByTestId("file-input").setInputFiles(dataset);
  await page.getByTestId("preview-button").click();
  await expect(page.getByTestId("preview-summary")).toContainText("300");
  await page.getByTestId("confirm-import").click();
  await page.getByTestId("start-analysis").click();
  await expect(page.getByTestId("service-error")).toContainText("算法服务不可用");
  await expect(page.getByTestId("service-error")).toContainText("重试");
});
