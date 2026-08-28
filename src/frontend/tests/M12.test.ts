import { cleanup, render, screen, waitFor } from "@testing-library/vue";
import { afterEach, describe, expect, it, vi } from "vitest";

import DataBackupPanel from "../src/DataBackupPanel.vue";
import type { CurrentUser } from "../src/auth";

const admin: CurrentUser = {
  user_id: "admin-1",
  username: "system.admin",
  display_name: "系统管理员",
  global_role: "SYSTEM_ADMIN",
  must_change_password: false,
};

const analyst: CurrentUser = {
  ...admin,
  user_id: "analyst-1",
  username: "analyst",
  display_name: "分析人员",
  global_role: "NONE",
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("M12 数据与备份管理界面", () => {
  it("系统管理员可查看容量、恢复点、完整校验与 Windows 操作入口", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/v1/admin/data-backup-status");
      return response({
        database_size_bytes: 25 * 1024 * 1024,
        deployment_mode: "LOCAL_NATIVE",
        backup_management: "WINDOWS_NATIVE_SCRIPTS",
        recovery_point_count: 3,
        recovery_points: [{
          backup_file: "alert-20260826.dump",
          created_at: "2026-08-26T02:30:00Z",
          size_bytes: 25 * 1024 * 1024,
          origin_instance_id: "0123456789abcdef0123456789abcdef",
          status: "METADATA_OK",
          message: "元数据和大小一致；请运行完整校验。",
        }],
        latest_success_at: "2026-08-26T02:30:00Z",
        total_backup_bytes: 75 * 1024 * 1024,
        all_hashes_valid: true,
        operator_instructions: ["恢复前先执行隔离恢复校验。"],
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(DataBackupPanel, { props: { user: admin } });

    expect(await screen.findAllByText("25 MiB")).toHaveLength(2);
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText(/完整校验通过/)).toBeInTheDocument();
    expect(screen.getByText("Windows 本机原生部署")).toBeInTheDocument();
    expect(screen.getByText("Windows 原生备份脚本")).toBeInTheDocument();
    expect(screen.getByTestId("recovery-point-table")).toBeInTheDocument();
    expect(screen.getByText("alert-20260826.dump")).toBeInTheDocument();
    expect(screen.getByText("元数据可用")).toBeInTheDocument();
    expect(screen.getByText("恢复前先执行隔离恢复校验。")).toBeInTheDocument();
    expect(screen.getByText("scripts\\backup-status.ps1")).toBeInTheDocument();
    expect(screen.getByText("scripts\\backup.ps1")).toBeInTheDocument();
    expect(screen.getByText("scripts\\restore-verify.ps1")).toBeInTheDocument();
    expect(screen.getByText("scripts\\backup-schedule.ps1")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("哈希状态为空时明确要求运行完整校验，不冒充校验通过", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({
      database_size_bytes: 0,
      deployment_mode: "DOCKER_COMPOSE",
      backup_management: "DEPLOYMENT_MANAGED",
      recovery_point_count: 0,
      recovery_points: [],
      latest_success_at: null,
      total_backup_bytes: 0,
      all_hashes_valid: null,
      operator_instructions: [],
    })));
    render(DataBackupPanel, { props: { user: admin } });

    expect(await screen.findByText(/待由部署管理员执行完整校验/)).toBeInTheDocument();
    expect(screen.queryByText(/完整校验通过/)).not.toBeInTheDocument();
    expect(screen.getByText("暂无成功备份")).toBeInTheDocument();
    expect(screen.getByText("当前没有可列出的恢复点。")).toBeInTheDocument();
    expect(screen.getByText("当前环境的备份与恢复由部署管理员按对应部署说明管理。")).toBeInTheDocument();
    expect(screen.queryByText("scripts\\backup-status.ps1")).not.toBeInTheDocument();
  });

  it("源码本机模式由部署环境管理备份时不显示发布包脚本", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({
      database_size_bytes: 1024,
      deployment_mode: "LOCAL_NATIVE",
      backup_management: "DEPLOYMENT_MANAGED",
      recovery_point_count: 0,
      recovery_points: [],
      latest_success_at: null,
      total_backup_bytes: 0,
      all_hashes_valid: null,
      operator_instructions: ["请使用当前部署方式对应的备份流程。"],
    })));
    render(DataBackupPanel, { props: { user: admin } });

    expect(await screen.findByText("Windows 本机原生部署")).toBeInTheDocument();
    expect(screen.getByText("由部署环境管理")).toBeInTheDocument();
    expect(screen.getByText(/待由部署管理员执行完整校验/)).toBeInTheDocument();
    expect(screen.getByText("当前环境的备份与恢复由部署管理员按对应部署说明管理。")).toBeInTheDocument();
    expect(screen.queryByText("scripts\\backup-status.ps1")).not.toBeInTheDocument();
  });

  it("非系统管理员不显示区块且不会请求管理接口", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(DataBackupPanel, { props: { user: analyst } });

    expect(screen.queryByText("数据与备份")).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled());
  });
});

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
