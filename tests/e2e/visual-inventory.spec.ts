import { expect, type Page, type APIResponse, type Response } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

import { test } from "./test-fixtures";
import { captureVisualState, prepareVisualPage } from "./visual-audit";

const repositoryRoot = path.resolve(__dirname, "../..");
const validDataset = path.resolve(repositoryRoot, process.env.E2E_DATASET ?? "samples/smoke/synthetic_smoke_utf8.csv");
const invalidDataset = path.resolve(repositoryRoot, process.env.E2E_INVALID_DATASET ?? "samples/invalid/invalid_enum.csv");

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  expect(value, `视觉验收缺少环境变量 ${name}`).toBeTruthy();
  return value!;
}

function passwordFromFile(name: string): string {
  const password = readFileSync(path.resolve(requiredEnvironment(name)), "utf8").trim();
  expect(password, `${name} 指向的密码文件不能为空`).not.toBe("");
  return password;
}

async function responseJson<T>(response: APIResponse | Response): Promise<T> {
  const body = await response.text();
  expect(response.ok(), body).toBeTruthy();
  return JSON.parse(body) as T;
}

async function previewFile(page: Page, file: string): Promise<{ batch_id: string; status: string }> {
  await page.getByTestId("file-input").setInputFiles(file);
  const response = page.waitForResponse((item) =>
    item.url().endsWith("/api/v1/imports/preview") && item.request().method() === "POST");
  await page.getByTestId("preview-button").click();
  return responseJson(await response);
}

test("真实业务闭环生成桌面与窄屏视觉清单", async ({ page }) => {
  const username = process.env.E2E_ADMIN_USERNAME ?? "admin";
  const currentPassword = passwordFromFile("E2E_ADMIN_PASSWORD_FILE");
  const newPassword = passwordFromFile("E2E_ADMIN_NEW_PASSWORD_FILE");
  const projectCode = requiredEnvironment("E2E_PROJECT_CODE");
  expect(newPassword).not.toBe(currentPassword);

  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await prepareVisualPage(page);
  await expect(page.getByRole("heading", { name: "登录报警管理系统" })).toBeVisible();
  await captureVisualState(page, "00-login");

  await page.getByTestId("login-username").fill(username);
  await page.getByTestId("login-password").fill(currentPassword);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("password-form")).toBeVisible();
  await captureVisualState(page, "01-first-password-change", page.getByTestId("password-form"));

  const passwordForm = page.getByTestId("password-form");
  await passwordForm.getByLabel("当前密码").fill(currentPassword);
  await passwordForm.getByLabel("新密码", { exact: true }).fill(newPassword);
  await passwordForm.getByLabel("再次输入新密码").fill(newPassword);
  await passwordForm.getByRole("button", { name: "保存新密码" }).click();
  await expect(page.getByRole("heading", { name: "选择当前工作项目" })).toBeVisible();

  await page.getByRole("button", { name: "新建项目" }).click();
  const projectForm = page.getByTestId("project-entry");
  await projectForm.getByLabel("项目编号（必填）").fill(projectCode);
  await projectForm.getByLabel("项目名称（必填）").fill("视觉验收项目");
  await projectForm.getByLabel("客户名称（必填）").fill("合成示例客户");
  await projectForm.getByLabel("厂区（必填）").fill("合成示例厂区");
  await projectForm.getByLabel("装置（必填）").fill("合成示例装置");
  await projectForm.getByRole("button", { name: "创建并选中" }).click();
  await expect(page.getByTestId(`select-project-${projectCode}`)).toHaveText("当前项目");
  await captureVisualState(page, "02-project", page.getByLabel("当前项目"));

  await page.getByText("账号与项目权限", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "账号管理" })).toBeVisible();
  await captureVisualState(page, "03-accounts-members", page.getByRole("heading", { name: "账号管理" }));

  await page.getByText("数据与备份", { exact: true }).click();
  await expect(page.getByLabel("数据与备份摘要")).toBeVisible();
  await captureVisualState(page, "04-backup-status", page.getByRole("heading", { name: "数据容量与恢复点" }));

  const invalid = await previewFile(page, invalidDataset);
  expect(invalid.status).toBe("REJECTED");
  await expect(page.getByTestId("import-error-dialog")).toBeVisible();
  await captureVisualState(page, "05-invalid-file-dialog", page.getByTestId("import-error-dialog"));
  await page.getByRole("button", { name: "关闭校验结果" }).click();

  const ready = await previewFile(page, validDataset);
  expect(ready.status).toBe("READY");
  await expect(page.getByTestId("mapping-editor")).toBeVisible();
  await captureVisualState(page, "06-mapping-ready-preview", page.getByTestId("mapping-editor"));

  const confirmResponse = page.waitForResponse((item) => item.url().includes(`/api/v1/imports/${ready.batch_id}/confirm`));
  await page.getByTestId("confirm-import").click();
  expect((await responseJson<{ status: string }>(await confirmResponse)).status).toBe("IMPORTED");
  await captureVisualState(page, "07-imported-analysis-entry", page.getByRole("heading", { name: "本次分析参数" }));

  const analysisResponse = page.waitForResponse((item) =>
    item.url().includes(`/api/v1/imports/${ready.batch_id}/analyses`) && item.request().method() === "POST");
  await page.getByTestId("start-analysis").click();
  expect((await analysisResponse).ok()).toBeTruthy();
  await expect(page.getByTestId("dashboard-total")).toBeVisible();
  await captureVisualState(page, "08-dashboard-overview", page.getByRole("heading", { name: "分析总览" }));
  await captureVisualState(page, "09-dashboard-trend-ratio", page.getByRole("img", { name: "每小时报警数量趋势" }));

  await page.getByTestId("filter-cause").selectOption("EQUIPMENT_FAULT");
  await page.getByRole("button", { name: "应用筛选" }).click();
  await expect(page.getByTestId("alarm-row-222")).toBeVisible();
  await captureVisualState(page, "10-alarm-table", page.getByRole("heading", { name: "报警列表" }));

  await page.getByTestId("alarm-row-222").click();
  await expect(page.getByTestId("alarm-detail")).toBeVisible();
  await expect(page.getByTestId("detail-evidence")).not.toBeEmpty();
  await expect(page.getByTestId("detail-event-chains")).toContainText("不代表已确认根因");
  await captureVisualState(page, "11-alarm-detail-evidence-chain", page.getByTestId("alarm-detail"));

  await page.getByTestId("classification-noise").selectOption("CHATTER");
  await page.getByTestId("classification-alarm-class").selectOption("NUISANCE");
  await page.getByTestId("classification-cause").selectOption("INSTRUMENT_ISSUE");
  await page.getByTestId("classification-reason").fill("演示项目视觉验收分类修订");
  await page.getByTestId("classification-save").click();
  await expect(page.getByTestId("classification-effective")).toContainText("抖动报警");
  await page.getByTestId("disposition-assignee").fill(username);
  await page.getByTestId("disposition-note").fill("演示项目视觉验收开始处置");
  await page.getByTestId("disposition-start").click();
  await expect(page.getByTestId("disposition-history")).toContainText("待处理 → 处理中");
  await captureVisualState(page, "12-classification-disposition", page.getByTestId("alarm-detail"));

  await expect(page.getByRole("heading", { name: "报告、审计与演示数据维护" })).toBeVisible();
  await page.getByTestId("audit-refresh").click();
  await expect(page.getByTestId("audit-table")).toBeVisible();
  await captureVisualState(page, "13-report-audit-reset", page.getByRole("heading", { name: "报告、审计与演示数据维护" }));
});
