import { expect, type APIResponse, type Page, type Response } from "@playwright/test";
import { mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { injectNextFetchFailure, test } from "./test-fixtures";

test.setTimeout(600_000);

const repositoryRoot = path.resolve(__dirname, "../..");
const mode = process.env.E2E_MODE ?? "smoke";
const expectedTotal = Number(process.env.E2E_EXPECTED_TOTAL ?? "300");
const cycles = Number(process.env.E2E_CYCLES ?? "2");
const dataset = path.resolve(
  repositoryRoot,
  process.env.E2E_DATASET ?? "samples/smoke/synthetic_smoke_utf8.csv",
);
const outputRoot = path.resolve(
  process.env.M5_OUTPUT_DIR ?? path.join(repositoryRoot, ".runtime/m5"),
);
const operator = "SYNTHETIC_M5_REVIEWER";

type PreviewResponse = { batch_id: string; total_rows: number; status: string };
type AnalysisResponse = { run_id: string; status: string };
type DashboardResponse = {
  total: number;
  disposition_counts: Record<string, number>;
  noise_type_counts: Record<string, number>;
  cause_category_counts: Record<string, number>;
};
type AuditItem = {
  event_id: string;
  event_type: string;
  occurred_at: string;
  operator: string;
  target_type: string;
  target_id: string;
  result: string;
  trace_id: string;
  details: Record<string, unknown>;
};
type AuditPage = { page: number; size: number; total: number; items: AuditItem[] };
type ResetResponse = {
  completed_at: string;
  business_state: string;
  deleted_counts: Record<string, number>;
};
type ReportMetric = { format: "pdf" | "xlsx"; duration_ms: number; bytes: number; file: string };

async function responseJson<T>(response: APIResponse | Response): Promise<T> {
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as T;
}

async function importAndAnalyze(page: Page): Promise<string> {
  await page.getByTestId("file-input").setInputFiles(dataset);
  const previewPromise = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/imports/preview")
      && response.request().method() === "POST",
  );
  await page.getByTestId("preview-button").click();
  const preview = await responseJson<PreviewResponse>(await previewPromise);
  expect(preview.status).toBe("READY");
  expect(preview.total_rows).toBe(expectedTotal);

  const confirmPromise = page.waitForResponse(
    (response) => response.url().includes(`/api/v1/imports/${preview.batch_id}/confirm`),
  );
  await page.getByTestId("confirm-import").click();
  expect((await responseJson<{ status: string }>(await confirmPromise)).status).toBe("IMPORTED");

  const analysisPromise = page.waitForResponse(
    (response) => response.url().includes(`/api/v1/imports/${preview.batch_id}/analyses`)
      && response.request().method() === "POST",
  );
  await page.getByTestId("start-analysis").click();
  const analysis = await responseJson<AnalysisResponse>(await analysisPromise);
  expect(analysis.status).toBe("COMPLETED");
  await expect(page.getByTestId("dashboard-total")).toContainText(String(expectedTotal));
  return analysis.run_id;
}

async function openSyntheticChainAlarm(page: Page): Promise<void> {
  await page.getByTestId("filter-cause").selectOption("EQUIPMENT_FAULT");
  await page.getByRole("button", { name: "应用筛选" }).click();
  await page.getByTestId("alarm-row-222").click();
  await expect(page.getByTestId("detail-source-row")).toContainText("222");
}

async function overrideClassification(page: Page, runId: string): Promise<void> {
  await page.getByTestId("classification-noise").selectOption("CHATTER");
  await page.getByTestId("classification-alarm-class").selectOption("NUISANCE");
  await page.getByTestId("classification-cause").selectOption("INSTRUMENT_ISSUE");
  await page.getByTestId("classification-operator").fill(operator);
  await page.getByTestId("classification-reason").fill("[SYNTHETIC] 根据事件链完成审核修订");
  const responsePromise = page.waitForResponse(
    (response) => response.url().endsWith("/classification")
      && response.request().method() === "PATCH",
  );
  await page.getByTestId("classification-save").click();
  const detail = await responseJson<{
    noise_type: string;
    alarm_class: string;
    cause_category: string;
    algorithm_classification: Record<string, string>;
    classification_override: { operator: string; reason: string; updated_at: string };
  }>(await responsePromise);
  expect(detail.noise_type).toBe("CHATTER");
  expect(detail.alarm_class).toBe("NUISANCE");
  expect(detail.cause_category).toBe("INSTRUMENT_ISSUE");
  expect(detail.algorithm_classification.noise_type).toBe("NORMAL");
  expect(detail.classification_override.operator).toBe(operator);
  expect(detail.classification_override.reason).toContain("审核修订");
  expect(detail.classification_override.updated_at).toBeTruthy();

  const effectiveDashboard = await responseJson<DashboardResponse>(
    await page.request.get(`/api/v1/analyses/${runId}/dashboard`),
  );
  expect(effectiveDashboard.noise_type_counts.NORMAL).toBe(169);
  expect(effectiveDashboard.noise_type_counts.CHATTER).toBe(41);
  expect(effectiveDashboard.cause_category_counts.EQUIPMENT_FAULT).toBe(29);
  expect(effectiveDashboard.cause_category_counts.INSTRUMENT_ISSUE).toBe(31);
}

async function closeAlarm(page: Page): Promise<void> {
  await page.getByTestId("disposition-operator").fill(operator);
  await page.getByTestId("disposition-note").fill("[SYNTHETIC] 开始处理修订报警");
  await page.getByTestId("disposition-start").click();
  await page.getByTestId("disposition-note").fill("[SYNTHETIC] 完成审核并关闭");
  await page.getByTestId("disposition-close").click();
  await expect(page.getByTestId("disposition-history")).toContainText("OPEN → IN_PROGRESS");
  await expect(page.getByTestId("disposition-history")).toContainText("IN_PROGRESS → CLOSED");
}

async function downloadReport(
  page: Page,
  runId: string,
  format: "pdf" | "xlsx",
  outputDirectory: string,
): Promise<ReportMetric> {
  mkdirSync(outputDirectory, { recursive: true });
  const responsePromise = page.waitForResponse(
    (response) => response.url().endsWith(`/api/v1/analyses/${runId}/reports/${format}`)
      && response.request().method() === "POST",
    { timeout: 240_000 },
  );
  const downloadPromise = page.waitForEvent("download", { timeout: 240_000 });
  const started = Date.now();
  await page.getByTestId(`report-${format}`).click();
  const [response, download] = await Promise.all([responsePromise, downloadPromise]);
  expect(response.ok(), await response.text()).toBeTruthy();
  const expectedType = format === "pdf"
    ? "application/pdf"
    : "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  expect(response.headers()["content-type"]).toContain(expectedType);
  expect(download.suggestedFilename()).toMatch(new RegExp(`^alert-report-${runId}\\.${format}$`));
  expect(await download.failure()).toBeNull();

  const reportPath = path.join(outputDirectory, `report.${format}`);
  await download.saveAs(reportPath);
  const header = readFileSync(reportPath).subarray(0, 5).toString("latin1");
  expect(format === "pdf" ? header.startsWith("%PDF") : header.startsWith("PK")).toBeTruthy();
  const bytes = statSync(reportPath).size;
  expect(bytes).toBeGreaterThan(100);
  return { format, duration_ms: Date.now() - started, bytes, file: reportPath };
}

async function auditSnapshot(
  page: Page,
  requiredEventTypes: string[],
): Promise<{ counts: Record<string, number>; total: number }> {
  const audit = await responseJson<AuditPage>(
    await page.request.get("/api/v1/audit-events?page=0&size=200"),
  );
  expect(audit.total).toBe(audit.items.length);
  for (const item of audit.items) {
    expect(item.event_id).toBeTruthy();
    expect(item.event_type).toBeTruthy();
    expect(item.occurred_at).toBeTruthy();
    expect(item.operator).toBeTruthy();
    expect(item.target_type).toBeTruthy();
    expect(item.target_id).toBeTruthy();
    expect(item.result).toBeTruthy();
    expect(item.trace_id).toBeTruthy();
    expect(item.details).toBeTruthy();
  }
  const counts: Record<string, number> = {};
  for (const item of audit.items) counts[item.event_type] = (counts[item.event_type] ?? 0) + 1;
  for (const eventType of requiredEventTypes) {
    expect(counts[eventType]).toBeGreaterThan(0);
  }
  expect(counts.REPORT_EXPORTED).toBe(2);
  return { counts, total: audit.total };
}

async function assertReportFailureKeepsState(page: Page, runId: string): Promise<void> {
  await injectNextFetchFailure(page, `/api/v1/analyses/${runId}/reports/pdf`, {
    code: "REPORT_FAILED",
    message: "报告生成失败，请重试",
  });
  await page.getByTestId("report-pdf").click();
  await expect(page.getByTestId("report-message")).toContainText("失败");
  await expect(page.getByTestId("report-message")).toContainText("重试");
  await expect(page.getByTestId("dashboard-total")).toContainText("300");
  expect((await page.request.get(`/api/v1/analyses/${runId}/dashboard`)).ok()).toBeTruthy();
}

async function resetDemo(page: Page, injectFailure: boolean): Promise<ResetResponse> {
  await expect(page.getByTestId("reset-operator")).toHaveValue("demo-reviewer");
  await page.getByTestId("reset-confirmation").fill("RESET_DEMO");
  if (injectFailure) {
    await injectNextFetchFailure(page, "/api/v1/demo/reset", {
      code: "RESET_FAILED",
      message: "演示复位失败，请重试",
    });
    await page.getByTestId("reset-button").click();
    await expect(page.getByTestId("reset-message")).toContainText("失败");
    await expect(page.getByTestId("reset-message")).toContainText("重试");
    await expect(page.getByTestId("dashboard-total")).toContainText("300");
    await page.getByTestId("reset-confirmation").fill("RESET_DEMO");
  }

  const resetPromise = page.waitForResponse(
    (response) => response.url().endsWith("/api/v1/demo/reset")
      && response.request().method() === "POST",
  );
  await page.getByTestId("reset-button").click();
  const reset = await responseJson<ResetResponse>(await resetPromise);
  expect(reset.business_state).toBe("EMPTY");
  expect(reset.completed_at).toBeTruthy();
  expect(Object.keys(reset.deleted_counts).length).toBeGreaterThan(0);
  await expect(page.getByTestId("reset-message")).toContainText("已复位");
  await expect(page.getByTestId("empty-state").first()).toBeVisible();
  const audit = await responseJson<AuditPage>(await page.request.get("/api/v1/audit-events?page=0&size=1"));
  expect(audit.total).toBe(0);
  return reset;
}

test("报告、审计、人工修订和明确复位结果一致", async ({ page }) => {
  test.skip(mode !== "smoke", "仅 Smoke 模式执行两轮完整 G5 闭环");
  expect(Number.isInteger(cycles) && cycles > 0, "E2E_CYCLES 必须是正整数").toBeTruthy();
  mkdirSync(outputRoot, { recursive: true });
  const summaries: Array<Record<string, unknown>> = [];

  for (let cycle = 1; cycle <= cycles; cycle += 1) {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "报警管理系统" })).toBeVisible();
    await expect(page.getByText("本地演示身份 demo-reviewer", { exact: false }).first()).toBeVisible();
    const runId = await importAndAnalyze(page);
    await openSyntheticChainAlarm(page);
    await overrideClassification(page, runId);
    await closeAlarm(page);

    await page.getByTestId("report-operator").fill(operator);
    const reportDirectory = path.join(outputRoot, `cycle-${cycle}`);
    const reports = [
      await downloadReport(page, runId, "pdf", reportDirectory),
      await downloadReport(page, runId, "xlsx", reportDirectory),
    ];

    await page.getByTestId("audit-filter").selectOption("REPORT_EXPORTED");
    await page.getByTestId("audit-refresh").click();
    await expect(page.getByTestId("audit-table")).toContainText("REPORT_EXPORTED");
    await expect(page.getByTestId("audit-table")).toContainText(operator);
    const audit = await auditSnapshot(page, [
      "IMPORT_CREATED",
      "IMPORT_CONFIRMED",
      "ANALYSIS_STARTED",
      "ANALYSIS_COMPLETED",
      "RESULT_OVERRIDDEN",
      "DISPOSITION_CHANGED",
      "REPORT_EXPORTED",
    ]);
    if (cycle === 1) {
      await assertReportFailureKeepsState(page, runId);
      const afterFailure = await auditSnapshot(page, ["REPORT_EXPORTED"]);
      expect(afterFailure.counts.REPORT_EXPORTED).toBe(2);
    }

    const dashboard = await responseJson<DashboardResponse>(
      await page.request.get(`/api/v1/analyses/${runId}/dashboard`),
    );
    const reset = await resetDemo(page, cycle === 1);
    summaries.push({
      total: dashboard.total,
      disposition_counts: dashboard.disposition_counts,
      noise_type_counts: dashboard.noise_type_counts,
      cause_category_counts: dashboard.cause_category_counts,
      audit,
      reset_deleted_counts: reset.deleted_counts,
      report_formats: reports.map((report) => report.format),
    });
  }

  for (const summary of summaries.slice(1)) {
    expect(summary).toEqual(summaries[0]);
  }
  writeFileSync(
    path.join(outputRoot, "smoke-normalized-summary.json"),
    `${JSON.stringify(summaries[0], null, 2)}\n`,
    "utf8",
  );
});

test("20000 行完成两类报告并记录可下载指标", async ({ page }) => {
  test.skip(mode !== "demo", "仅 Demo 模式执行 20000 行报告门槛");
  mkdirSync(outputRoot, { recursive: true });
  await page.goto("/");
  const runId = await importAndAnalyze(page);
  const dashboard = await responseJson<DashboardResponse>(
    await page.request.get(`/api/v1/analyses/${runId}/dashboard`),
  );
  expect(dashboard.total).toBe(20_000);
  await page.getByTestId("report-operator").fill(operator);
  const reportDirectory = path.join(outputRoot, "demo-20000");
  const reports = [
    await downloadReport(page, runId, "pdf", reportDirectory),
    await downloadReport(page, runId, "xlsx", reportDirectory),
  ];
  expect((await auditSnapshot(page, [
    "IMPORT_CREATED",
    "IMPORT_CONFIRMED",
    "ANALYSIS_STARTED",
    "ANALYSIS_COMPLETED",
    "REPORT_EXPORTED",
  ])).counts.REPORT_EXPORTED).toBe(2);
  const reset = await resetDemo(page, false);
  writeFileSync(
    path.join(outputRoot, "demo-20000-metrics.json"),
    `${JSON.stringify({ total: dashboard.total, reports, reset_deleted_counts: reset.deleted_counts }, null, 2)}\n`,
    "utf8",
  );
});
