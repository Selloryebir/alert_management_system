import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/vue";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../src/App.vue";
import ManualAlarmPanel from "../src/ManualAlarmPanel.vue";
import ProjectWorkspace from "../src/ProjectWorkspace.vue";
import ReviewOperations from "../src/ReviewOperations.vue";
import { apiFetch, apiJson, setCsrfToken, setUnauthorizedHandler } from "../src/api";
import { changePassword } from "../src/auth";
import { updateClassification, updateDisposition } from "../src/business";
import { invalidateManualAlarm, updateManualAlarm } from "../src/projects";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setUnauthorizedHandler(undefined);
});

describe("M11 同源请求和身份入口", () => {
  it("统一请求层携带同源会话和服务端指定的 CSRF 请求头", async () => {
    setCsrfToken({ token: "csrf-value", header_name: "X-SECURITY-CSRF", parameter_name: "_csrf" });
    const fetchMock = vi.fn(async () => new Response("{}", {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await apiFetch("/api/v1/example", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.credentials).toBe("same-origin");
    expect(new Headers(init.headers).get("X-SECURITY-CSRF")).toBe("csrf-value");
  });

  it("401 清除内存身份，403 保留服务端中文原因", async () => {
    const unauthorized = vi.fn();
    setUnauthorizedHandler(unauthorized);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      code: "AUTH_REQUIRED",
      message: "请重新登录。",
    }), { status: 401, headers: { "Content-Type": "application/json" } })));
    await expect(apiJson(await apiFetch("/api/v1/projects"))).rejects.toThrow("请重新登录");
    expect(unauthorized).toHaveBeenCalledOnce();

    await expect(apiJson(new Response(JSON.stringify({ message: "当前账号不是项目负责人。" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    }))).rejects.toThrow("当前账号不是项目负责人");
  });

  it("临时密码用户只显示改密和退出，不渲染业务工作台", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return response({ components: {} });
      if (url === "/api/v1/auth/csrf") return response({ token: "csrf", header_name: "X-CSRF-TOKEN", parameter_name: "_csrf" });
      if (url === "/api/v1/auth/me") return response({
        user_id: "user-1",
        username: "first.user",
        display_name: "首次用户",
        global_role: "NONE",
        must_change_password: true,
      });
      throw new Error(`未处理请求 ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(App);

    expect(await screen.findByText(/当前使用临时密码/)).toBeInTheDocument();
    expect(screen.getByTestId("password-form")).toBeInTheDocument();
    expect(screen.getByTestId("logout-button")).toBeInTheDocument();
    expect(screen.queryByText("选择当前工作项目")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).startsWith("/api/v1/projects"))).toBe(false);
  });

  it("改密使旧会话失效后以新密码重新登录", async () => {
    const urls: string[] = [];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      urls.push(url);
      if (url === "/api/v1/auth/password") return response({});
      if (url === "/api/v1/auth/csrf") return response({ token: `csrf-${urls.length}`, header_name: "X-CSRF-TOKEN", parameter_name: "_csrf" });
      if (url === "/api/v1/auth/login") {
        expect(JSON.parse(String(init?.body))).toEqual({ username: "first.user", password: "NewPassword-123" });
        return response({});
      }
      if (url === "/api/v1/auth/me") return response({
        user_id: "user-1", username: "first.user", display_name: "首次用户",
        global_role: "NONE", must_change_password: false,
      });
      throw new Error(`未处理请求 ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = await changePassword("first.user", "Temporary-123", "NewPassword-123");

    expect(user.must_change_password).toBe(false);
    expect(urls).toEqual([
      "/api/v1/auth/password",
      "/api/v1/auth/csrf",
      "/api/v1/auth/login",
      "/api/v1/auth/csrf",
      "/api/v1/auth/me",
    ]);
  });
});

describe("M11 角色可见性和真实操作者", () => {
  it("分析人员看不到项目管理动作，仍可选择授权项目", async () => {
    const project = {
      project_id: "project-1", code: "PRJ-001", name: "授权项目", client_name: "客户",
      site: "厂区", unit_name: "装置", status: "ACTIVE", report_title: "报告", report_fields: ["summary"],
      validation_rules: { required_fields: [] }, project_role: "ANALYST",
      created_at: "2026-08-26T00:00:00Z", updated_at: "2026-08-26T00:00:00Z",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/v1/projects?")) return response([project]);
      if (url.endsWith("/overview")) return response({
        project_id: project.project_id,
        statistics: { batch_count: 0, alarm_count: 0, valid_alarm_count: 0, invalid_alarm_count: 0, pending_disposition_count: 0 },
        recent_tasks: [],
      });
      throw new Error(`未处理请求 ${url}`);
    }));
    render(ProjectWorkspace, { props: { systemAdmin: false } });

    expect(await screen.findByText("当前：授权项目")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新建项目" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "项目设置" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "归档项目" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出项目清单" })).toBeInTheDocument();
  });

  it("动作身份输入已删除，报警源操作员仍是业务字段", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response([])));
    render(ManualAlarmPanel, { props: { projectId: "project-1" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "人工补录" })).toBeEnabled());
    await fireEvent.click(screen.getByRole("button", { name: "人工补录" }));
    expect(screen.getByLabelText(/源操作员/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/修订操作者|作废操作者/)).not.toBeInTheDocument();

    cleanup();
    render(ReviewOperations, { props: { projectId: "project-1", canManage: false, systemAdmin: false } });
    expect(screen.queryByLabelText(/报告操作者/)).not.toBeInTheDocument();
    expect(screen.queryByText("只读审计记录")).not.toBeInTheDocument();
    expect(screen.queryByText(/复位演示数据/)).not.toBeInTheDocument();
  });

  it("分类、处置、修订、作废请求不携带客户端动作身份", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return response({});
    }));

    await updateClassification("run-1", "record-1", {
      noise_type: "CHATTER", alarm_class: "NUISANCE", cause_category: "INSTRUMENT_ISSUE",
    }, "复核理由");
    await updateDisposition("run-1", "record-1", "IN_PROGRESS", "user-2", "开始处理");
    await updateManualAlarm("project-1", "record-1", { description: "修订", operator: "源操作员", reason: "现场复核" });
    await invalidateManualAlarm("project-1", "record-1", "重复补录");

    expect(bodies).toEqual([
      { noise_type: "CHATTER", alarm_class: "NUISANCE", cause_category: "INSTRUMENT_ISSUE", reason: "复核理由" },
      { status: "IN_PROGRESS", assignee: "user-2", note: "开始处理" },
      { description: "修订", operator: "源操作员", reason: "现场复核" },
      { reason: "重复补录" },
    ]);
    for (const body of bodies) {
      expect(body).not.toHaveProperty("edited_by");
    }
  });
});

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
