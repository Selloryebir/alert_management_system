import { expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

import { test } from "./test-fixtures";

function requiredEnvironment(name: string): string {
  const value = process.env[name]?.trim();
  expect(value, `发布候选验收缺少环境变量 ${name}`).toBeTruthy();
  return value!;
}

function passwordFromEnvironment(name: string): string {
  const file = path.resolve(requiredEnvironment(name));
  const password = readFileSync(file, "utf8").trim();
  expect(password, `${name} 指向的密码文件不能为空`).not.toBe("");
  expect(password.length, `${name} 中的密码长度必须为 12–64 个字符`).toBeGreaterThanOrEqual(12);
  expect(password.length, `${name} 中的密码长度必须为 12–64 个字符`).toBeLessThanOrEqual(64);
  expect(Buffer.byteLength(password, "utf8"), `${name} 中的密码不得超过 72 个 UTF-8 字节`).toBeLessThanOrEqual(72);
  return password;
}

async function loginThroughPage(page: Page, password: string): Promise<void> {
  const username = process.env.E2E_ADMIN_USERNAME ?? "admin";
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "登录报警管理系统" })).toBeVisible();
  await page.getByTestId("login-username").fill(username);
  await page.getByTestId("login-password").fill(password);
  await page.getByTestId("login-submit").click();
  await expect(page.getByText(`${username} · 系统管理员`, { exact: false })).toBeVisible();
}

async function openBackupPanel(page: Page): Promise<void> {
  await page.getByText("数据与备份", { exact: true }).click();
  await expect(page.getByRole("heading", { name: "数据容量与恢复点" })).toBeVisible();
  await expect(page.getByLabel("数据与备份摘要")).toBeVisible();
  await expect(page.getByText("Windows 本机原生部署", { exact: true })).toBeVisible();
  await expect(page.getByText("Windows 原生备份脚本", { exact: true })).toBeVisible();
}

test("发布候选首次登录通过页面改密、新建项目并查看初始备份状态", async ({ page }) => {
  const currentPassword = passwordFromEnvironment("E2E_ADMIN_PASSWORD_FILE");
  const newPassword = passwordFromEnvironment("E2E_ADMIN_NEW_PASSWORD_FILE");
  const projectCode = requiredEnvironment("E2E_PROJECT_CODE");
  expect(newPassword !== currentPassword, "新密码不得等于当前密码").toBeTruthy();

  await loginThroughPage(page, currentPassword);
  await expect(page.getByText("当前使用临时密码。修改密码后才能访问项目和业务数据。", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "选择当前工作项目" })).toHaveCount(0);

  const passwordForm = page.getByTestId("password-form");
  await passwordForm.getByLabel("当前密码").fill(currentPassword);
  await passwordForm.getByLabel("新密码", { exact: true }).fill(newPassword);
  await passwordForm.getByLabel("再次输入新密码").fill(newPassword);
  await passwordForm.getByRole("button", { name: "保存新密码" }).click();
  await expect(page.getByText("密码已更新，其他旧会话已失效。", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "选择当前工作项目" })).toBeVisible();

  await page.getByRole("button", { name: "新建项目" }).click();
  const projectForm = page.getByTestId("project-entry");
  await projectForm.getByLabel("项目编号（必填）").fill(projectCode);
  await projectForm.getByLabel("项目名称（必填）").fill("发布候选业务终验项目");
  await projectForm.getByLabel("客户名称（必填）").fill("合成示例客户");
  await projectForm.getByLabel("厂区（必填）").fill("合成示例厂区");
  await projectForm.getByLabel("装置（必填）").fill("合成示例装置");
  await projectForm.getByRole("button", { name: "创建并选中" }).click();
  await expect(page.getByText(`项目“发布候选业务终验项目”已创建并选中。`, { exact: true })).toBeVisible();
  await expect(page.getByTestId(`select-project-${projectCode}`)).toHaveText("当前项目");

  await openBackupPanel(page);
  const summary = page.getByLabel("数据与备份摘要");
  await expect(summary).toContainText("恢复点数0");
  await expect(summary).toContainText("暂无成功备份");
  await expect(page.getByText("当前没有可列出的恢复点。", { exact: true })).toBeVisible();
  await expect(page.getByText("SHA-256 状态：待运行 backup-status.ps1 完整校验", { exact: true })).toBeVisible();
});

test("发布候选备份生成后通过页面显示恢复点和待完整校验边界", async ({ page }) => {
  const password = passwordFromEnvironment("E2E_ADMIN_PASSWORD_FILE");
  const expectedRecoveryPoints = Number(process.env.E2E_EXPECTED_RECOVERY_POINTS ?? "1");
  expect(Number.isInteger(expectedRecoveryPoints) && expectedRecoveryPoints > 0,
    "E2E_EXPECTED_RECOVERY_POINTS 必须是正整数").toBeTruthy();

  await loginThroughPage(page, password);
  await openBackupPanel(page);

  const summary = page.getByLabel("数据与备份摘要");
  await expect(summary).toContainText(`恢复点数${expectedRecoveryPoints}`);
  await expect(summary).not.toContainText("暂无成功备份");
  const recoveryTable = page.getByTestId("recovery-point-table");
  await expect(recoveryTable).toBeVisible();
  await expect(recoveryTable.locator("tbody tr")).toHaveCount(expectedRecoveryPoints);
  await expect(recoveryTable).toContainText(".dump");
  await expect(recoveryTable).toContainText("元数据可用");
  await expect(page.getByText("SHA-256 状态：待运行 backup-status.ps1 完整校验", { exact: true })).toBeVisible();
  await expect(page.getByText("scripts\\backup-status.ps1", { exact: true })).toBeVisible();
  await expect(page.getByText("scripts\\restore-verify.ps1", { exact: true })).toBeVisible();
});
