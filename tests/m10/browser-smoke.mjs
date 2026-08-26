#!/usr/bin/env node

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "../e2e/node_modules/playwright/index.mjs";

const baseUrl = process.env.E2E_BASE_URL ?? "http://127.0.0.1:8080";
const dataset = process.env.E2E_DATASET;
const correctionDataset = process.env.M10_CORRECTION_DATASET;
const outputDir = process.env.M10_OUTPUT_DIR;
assert(dataset && fs.existsSync(dataset), `E2E_DATASET 不存在：${dataset ?? "未设置"}`);
assert(
  correctionDataset && fs.existsSync(correctionDataset),
  `M10_CORRECTION_DATASET 不存在：${correctionDataset ?? "未设置"}`,
);
assert(outputDir, "M10_OUTPUT_DIR 未设置");
fs.mkdirSync(outputDir, { recursive: true });

const suffix = `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
const projectCode = `M10-UI-${suffix}`;
const projectName = `M10浏览器项目-${suffix}`;
const reportTitle = `M10浏览器报告-${suffix}`;
const machineEnums = /\b(?:ACTIVE|ARCHIVED|READY|REJECTED|IMPORTED|ANALYZING|COMPLETED|FAILED|OPEN|IN_PROGRESS|CLOSED|NORMAL|DUPLICATE|CHATTER|SHORT_LIVED|PERSISTENT|NUISANCE|ACTIONABLE|STANDARD|PROCESS_DISTURBANCE|EQUIPMENT_FAULT|INSTRUMENT_ISSUE|MAINTENANCE_TEST|UNKNOWN|UP|DOWN)\b/;

async function visibleBody(page) {
  return (await page.locator("body").evaluate((body) => {
    const copy = body.cloneNode(true);
    if (!(copy instanceof HTMLElement)) return "";
    // 原始行是不可篡改的输入证据，不把其中的源系统机器值误判为页面枚举泄漏。
    copy.querySelectorAll('[data-testid="raw-payload"]').forEach((element) => element.remove());
    return copy.innerText;
  })).replaceAll("\u00a0", " ");
}

async function assertNoMachineEnums(page, context) {
  const text = await visibleBody(page);
  const match = text.match(machineEnums);
  const offset = match?.index ?? 0;
  assert(!match, `${context} 仍直接显示机器枚举：${match?.[0]}；上下文：${text.slice(Math.max(0, offset - 80), offset + 160)}`);
}

async function waitForCompletedGuide(page) {
  const guide = page.locator(".onboarding-panel");
  await guide.waitFor({ state: "visible" });
  const steps = guide.locator('[data-testid^="onboarding-step-"]');
  assert.equal(await steps.count(), 6, "首次使用引导必须固定呈现六步");
  for (let index = 1; index <= 6; index += 1) {
    const step = page.getByTestId(`onboarding-step-${index}`);
    assert((await step.getAttribute("class") ?? "").split(/\s+/).includes("done"), `首次引导第 ${index} 步未由真实状态完成`);
  }
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ acceptDownloads: true });
const page = await context.newPage();
const consoleErrors = [];
page.on("console", (message) => {
  if (message.type() === "error") consoleErrors.push(message.text());
});
page.on("pageerror", (error) => consoleErrors.push(error.message));

try {
  await page.goto(baseUrl, { waitUntil: "networkidle" });
  const guide = page.locator(".onboarding-panel");
  await guide.waitFor({ state: "visible" });
  assert.equal(await guide.locator('[data-testid^="onboarding-step-"]').count(), 6, "首页未呈现固定六步引导");

  await page.getByRole("button", { name: /新建项目/ }).click();
  await page.getByLabel("项目编号").fill(projectCode);
  await page.getByLabel("项目名称").fill(projectName);
  await page.getByLabel("客户名称").fill("M10 浏览器客户");
  await page.getByLabel("厂区").fill("M10 浏览器厂区");
  await page.getByLabel("装置").fill("M10 浏览器装置");
  await page.getByRole("button", { name: /^创建并选中$/ }).click();
  await page.getByRole("heading", { name: new RegExp(projectName) }).waitFor();
  await page.locator(".project-current").getByText("使用中").waitFor();

  await page.getByRole("button", { name: /项目设置/ }).click();
  await page.getByLabel("报告抬头").fill(reportTitle);
  const summaryField = page.getByLabel("分析摘要");
  if (!(await summaryField.isChecked())) await summaryField.check();
  await page.getByRole("button", { name: /保存.*设置/ }).click();
  await page.getByText(/项目资料、校验规则和报告字段已保存/).waitFor();

  const manualPanel = page.getByTestId("manual-alarm");
  await manualPanel.getByRole("button", { name: /人工补录/ }).click();
  await manualPanel.getByLabel(/发生时间/).fill("2026-08-26T10:00");
  await manualPanel.getByLabel(/位号/).fill(`M10-UI-MANUAL-${suffix}`);
  await manualPanel.getByLabel(/报警描述|描述/).fill("M10 浏览器人工补录");
  await manualPanel.getByLabel(/优先级/).selectOption("P2");
  await manualPanel.getByLabel(/状态/).selectOption("ACTIVE");
  await manualPanel.getByLabel(/来源系统/).fill("MANUAL_ENTRY");
  await manualPanel.getByLabel(/补录操作者/).fill("M10浏览器验收员");
  await manualPanel.getByRole("button", { name: /保存补录/ }).click();
  await manualPanel.getByText(/补录成功/).waitFor();
  await manualPanel.getByRole("button", { name: /编辑.*补录|修订/ }).first().click();
  await manualPanel.getByLabel(/报警描述|描述/).fill("M10 浏览器人工补录已修订");
  await manualPanel.getByLabel(/修订操作者/).fill("M10浏览器验收员");
  await manualPanel.getByLabel(/修订理由/).fill("浏览器验收修订");
  await manualPanel.getByRole("button", { name: /保存修订/ }).click();
  await manualPanel.getByText(/修订.*成功|修订已保存/).waitFor();
  await manualPanel.getByRole("button", { name: /作废/ }).first().click();
  await manualPanel.getByLabel(/作废操作者/).fill("M10浏览器验收员");
  await manualPanel.getByLabel(/作废理由/).fill("浏览器验收作废");
  await manualPanel.getByRole("button", { name: /确认作废/ }).click();
  await manualPanel.getByRole("status").filter({ hasText: /人工补录报警已作废/ }).waitFor();

  const fileInput = page.locator('input[type="file"]').first();
  await fileInput.setInputFiles(correctionDataset);
  await page.getByRole("button", { name: /读取表头并预览|按当前映射重新校验/ }).click();
  const correctionSummary = page.getByTestId("preview-summary");
  await correctionSummary.getByText("校验未通过", { exact: true }).waitFor();
  const priorityCorrection = page.getByTestId("correction-row-2-priority").locator("input");
  const valueCorrection = page.getByTestId("correction-row-2-value").locator("input");
  assert.equal(await priorityCorrection.inputValue(), "p9", "异常行优先级输入未回显原始值");
  assert.equal(await valueCorrection.inputValue(), "bad", "异常行当时值输入未回显原始值");
  await priorityCorrection.fill("P1");
  await valueCorrection.fill("88.5");
  await correctionSummary.getByRole("button", { name: /按修正值重新全量校验/ }).click();
  await correctionSummary.getByText("校验通过", { exact: true }).waitFor();
  const correctedRow = correctionSummary.locator("tbody tr").filter({ hasText: "M10-CORRECT-2" });
  assert(/P1（紧急）/.test(await correctedRow.innerText()), "异常行修正后预览未显示规范化优先级");
  await correctionSummary.getByRole("button", { name: /确认导入/ }).click();
  await page.locator(".import-panel .import-message").filter({ hasText: /已导入当前项目/ }).waitFor();

  await fileInput.setInputFiles(dataset);
  await page.getByRole("button", { name: /读取表头并预览|按当前映射重新校验/ }).click();
  const mapping = page.getByTestId("mapping-editor");
  await mapping.waitFor({ state: "visible" });
  assert.equal(await mapping.locator("textarea").count(), 0, "字段映射区不得提供 JSON 文本框");
  assert(!/JSON/i.test(await mapping.innerText()), "字段映射区不得要求业务人员理解 JSON");
  assert((await mapping.locator("select").count()) >= 5, "字段映射区未提供必要字段的图形化下拉选择");
  await page.getByText(/有效 300|300 条有效|300.*可导入/).waitFor({ timeout: 30_000 });
  await page.getByRole("button", { name: /确认导入/ }).click();
  await page.locator(".import-panel .import-message").filter({ hasText: /已导入当前项目/ }).waitFor({ timeout: 30_000 });
  await assertNoMachineEnums(page, "导入完成页面");

  await page.getByTestId("start-analysis").click();
  await page.getByTestId("dashboard-total").waitFor({ timeout: 120_000 });
  await page.locator(".alarm-browser tbody button.table-link").first().click();
  await page.getByTestId("alarm-detail").waitFor();
  await page.getByLabel(/责任人/).fill("浏览器甲班值长");
  await page.getByLabel(/处置操作者|操作者/).fill("M10浏览器验收员");
  await page.getByLabel(/处置说明/).fill("浏览器真实处置闭环");
  await page.getByRole("button", { name: /开始处理|处理中/ }).click();
  await page.getByRole("status").filter({ hasText: /处置已保存，当前已进入处理中/ }).waitFor();
  await assertNoMachineEnums(page, "报警详情与处置页面");

  await page.getByLabel(/报告操作者/).fill("M10浏览器验收员");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: /下载.*(?:PDF|XLSX)|生成.*(?:PDF|XLSX)/ }).first().click();
  const download = await downloadPromise;
  const downloadPath = path.join(outputDir, `browser-${download.suggestedFilename()}`);
  await download.saveAs(downloadPath);
  assert(fs.statSync(downloadPath).size > 500, "浏览器下载的项目报告为空或不可用");
  await waitForCompletedGuide(page);

  await page.getByRole("button", { name: /归档项目/ }).click();
  await page.locator(".project-current .compact-heading p").filter({ hasText: new RegExp(`${projectCode} · 已归档`) }).waitFor();
  assert.equal(await page.getByTestId("delete-project").count(), 0, "有业务数据的归档项目错误显示删除入口");
  assert.equal(await fileInput.isDisabled(), true, "归档项目页面仍允许导入写入");
  await page.getByRole("button", { name: /恢复项目/ }).click();
  await page.locator(".project-current").getByText("使用中").waitFor();
  assert.equal(await fileInput.isDisabled(), false, "恢复项目后未恢复导入能力");
  await assertNoMachineEnums(page, "项目归档与恢复页面");

  const emptyCode = `M10-UI-EMPTY-${suffix}`;
  const emptyName = `M10浏览器空项目-${suffix}`;
  await page.getByRole("button", { name: /新建项目/ }).click();
  const projectEntry = page.getByTestId("project-entry");
  await projectEntry.getByLabel(/项目编号/).fill(emptyCode);
  await projectEntry.getByLabel(/项目名称/).fill(emptyName);
  await projectEntry.getByLabel(/客户名称/).fill("M10 空项目客户");
  await projectEntry.getByLabel(/厂区/).fill("M10 空项目厂区");
  await projectEntry.getByLabel(/装置/).fill("M10 空项目装置");
  await projectEntry.getByRole("button", { name: /创建并选中/ }).click();
  await page.getByRole("heading", { name: new RegExp(emptyName) }).waitFor();
  await page.getByRole("button", { name: /归档项目/ }).click();
  const deleteProject = page.getByTestId("delete-project");
  await deleteProject.waitFor();
  await deleteProject.getByLabel(/输入项目编号以确认删除/).fill(emptyCode);
  await deleteProject.getByRole("button", { name: /^删除项目$/ }).click();
  await page.getByRole("status").filter({ hasText: new RegExp(`空项目“${emptyName}”已删除`) }).waitFor();
  assert.equal(await page.getByText(emptyName, { exact: true }).count(), 0, "已删除空项目仍残留在项目列表");

  assert.deepEqual(consoleErrors, [], `浏览器出现控制台错误：${consoleErrors.join("\n")}`);
  const evidence = {
    status: "PASS",
    project_code: projectCode,
    project_name: projectName,
    report_title: reportTitle,
    download: downloadPath,
    verified: [
      "six-step-real-state-guide",
      "graphical-mapping-without-json",
      "chinese-business-enums",
      "project-settings",
      "manual-edit-invalidate",
      "rejected-row-correction-controls",
      "analysis-detail-assignee-disposition-report",
      "browser-archive-and-restore",
      "browser-empty-archived-project-delete",
    ],
  };
  fs.writeFileSync(path.join(outputDir, "browser-result.json"), `${JSON.stringify(evidence, null, 2)}\n`);
  console.log(JSON.stringify(evidence, null, 2));
} catch (error) {
  await page.screenshot({ path: path.join(outputDir, "browser-failure.png"), fullPage: true }).catch(() => {});
  fs.writeFileSync(
    path.join(outputDir, "browser-failure.txt"),
    `${error instanceof Error ? error.stack : String(error)}\n`,
  );
  throw error;
} finally {
  await context.close();
  await browser.close();
}
