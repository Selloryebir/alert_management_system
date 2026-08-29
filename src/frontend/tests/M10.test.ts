import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/vue";
import { afterEach, describe, expect, it, vi } from "vitest";
import { defineComponent, ref } from "vue";

import App from "../src/App.vue";
import ManualAlarmPanel from "../src/ManualAlarmPanel.vue";
import ProjectWorkspace from "../src/ProjectWorkspace.vue";
import ReviewOperations from "../src/ReviewOperations.vue";
import { auditDetails, localizedEvidence, priorityLabel, zh } from "../src/labels";

const authenticatedUser = vi.hoisted(() => ({
  user_id: "00000000-0000-0000-0000-000000000099",
  username: "test-admin",
  display_name: "测试管理员",
  global_role: "SYSTEM_ADMIN" as const,
  must_change_password: false,
}));

vi.mock("../src/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/auth")>()),
  initializeCsrf: async () => ({ token: "test-csrf", header_name: "X-CSRF-TOKEN", parameter_name: "_csrf" }),
  currentUser: async () => ({ ...authenticatedUser }),
  logout: async () => undefined,
  changePassword: async () => ({ ...authenticatedUser }),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const project = {
  project_id: "11111111-1111-1111-1111-111111111111",
  code: "PRJ-001",
  name: "一号装置报警治理",
  client_name: "合成客户",
  site: "华东厂区",
  unit_name: "一号装置",
  status: "ACTIVE",
  report_title: "一号装置报警分析报告",
  report_fields: ["summary", "priority", "area", "unit", "noise", "cause", "disposition", "chains"],
  validation_rules: { required_fields: [], value_min: null, value_max: null, threshold_min: null, threshold_max: null },
  project_role: "SYSTEM_ADMIN" as const,
  created_at: "2026-08-26T08:00:00+08:00",
  updated_at: "2026-08-26T08:00:00+08:00",
};

const overview = {
  project_id: project.project_id,
  statistics: { batch_count: 2, alarm_count: 300, valid_alarm_count: 299, invalid_alarm_count: 1, pending_disposition_count: 20 },
  recent_tasks: [],
};

const health = {
  components: { system: { status: "UP" }, database: { status: "UP" }, algorithm: { status: "UP" } },
};

describe("M10 全中文项目化入口", () => {
  it("显示产品能力叙事、六步引导、独立页签和中文健康状态", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/v1/health") return { ok: true, json: async () => health } as Response;
      if (url.startsWith("/api/v1/projects?")) return { ok: true, json: async () => [] } as Response;
      throw new Error(`未处理请求 ${url}`);
    }));
    render(App);

    expect(screen.getByRole("heading", { name: "报警管理系统" })).toBeInTheDocument();
    expect(screen.getByText(/推动分析、处置与报告协同闭环/)).toBeInTheDocument();
    expect(screen.queryByText("仅使用合成数据")).not.toBeInTheDocument();
    expect(await screen.findByTestId("workspace-tab-dashboard")).toHaveAttribute("aria-selected", "true");
    expect(document.querySelector(".status-panel")).not.toBeVisible();
    for (let index = 1; index <= 6; index += 1) {
      expect(await screen.findByTestId(`onboarding-step-${index}`)).toBeInTheDocument();
    }
    await fireEvent.click(await screen.findByTestId("workspace-tab-status"));
    expect(screen.getByTestId("workspace-tab-status")).toHaveAttribute("aria-selected", "true");
    expect(document.querySelector(".status-panel")).toBeVisible();
    await waitFor(() => expect(screen.getAllByText("正常")).toHaveLength(3));
    expect(screen.queryByText("UP")).not.toBeInTheDocument();
  });

  it("创建项目并用真实 overview 展示统计", async () => {
    let created = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/v1/projects?")) return { ok: true, json: async () => created ? [project] : [] } as Response;
      if (url === "/api/v1/projects" && init?.method === "POST") { created = true; return { ok: true, json: async () => project } as Response; }
      if (url.endsWith("/overview")) return { ok: true, json: async () => overview } as Response;
      throw new Error(`未处理请求 ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(ProjectWorkspace, { props: { systemAdmin: true } });

    await fireEvent.click(screen.getByRole("button", { name: "新建项目" }));
    await fireEvent.update(screen.getByLabelText(/项目编号/), project.code);
    await fireEvent.update(screen.getByLabelText(/项目名称/), project.name);
    await fireEvent.update(screen.getByLabelText(/客户名称/), project.client_name);
    await fireEvent.update(screen.getByLabelText(/厂区/), project.site);
    await fireEvent.update(screen.getByLabelText(/装置/), project.unit_name);
    await fireEvent.click(screen.getByRole("button", { name: "创建并选中" }));

    expect(await screen.findByText(/已创建并选中/)).toBeInTheDocument();
    const current = screen.getByLabelText("当前项目");
    expect(within(current).getByText("300")).toBeInTheDocument();
    const posted = fetchMock.mock.calls.find(([url, init]) => String(url) === "/api/v1/projects" && init?.method === "POST");
    expect(JSON.parse(String(posted?.[1]?.body))).toMatchObject({ code: project.code, name: project.name });
  });

  it("按当前项目预览、图形化映射并确认导入，不出现 JSON 编辑器", async () => {
    const readyBatch = {
      batch_id: "22222222-2222-2222-2222-222222222222",
      project_id: project.project_id,
      file_name: "alarm.csv",
      format: "CSV",
      status: "READY",
      total_rows: 1,
      valid_rows: 1,
      error_count: 0,
      errors: [],
      headers: ["发生时间", "位号", "报警描述", "优先级", "状态", "厂区", "区域", "来源系统"],
      mapping: { event_time: "发生时间", tag: "位号" },
      preview_rows: [{ source_row: 2, tag: "PT-001", description: "压力高", priority: "P1", state: "ACTIVE" }],
      created_at: "2026-08-26T08:00:00+08:00",
    };
    let previewBody: FormData | undefined;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/health") return { ok: true, json: async () => health } as Response;
      if (url.startsWith("/api/v1/projects?")) return { ok: true, json: async () => [project] } as Response;
      if (url.endsWith("/overview")) return { ok: true, json: async () => overview } as Response;
      if (url.startsWith("/api/v1/imports?") ) return { ok: true, json: async () => [] } as Response;
      if (url === "/api/v1/imports/preview") { previewBody = init?.body as FormData; return { ok: true, json: async () => readyBatch } as Response; }
      if (url.endsWith("/confirm")) return { ok: true, json: async () => ({ ...readyBatch, status: "IMPORTED" }) } as Response;
      throw new Error(`未处理请求 ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(App);
    await fireEvent.click(await screen.findByTestId(`select-project-${project.code}`));
    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([input]) =>
        String(input) === `/api/v1/imports?limit=20&project_id=${project.project_id}`,
      );
      expect(request).toBeDefined();
      expect((request?.[1]?.headers as Headers).get("Accept")).toBe("application/json");
      expect(request?.[1]?.credentials).toBe("same-origin");
    });
    await fireEvent.click(screen.getByTestId("workspace-tab-import"));
    await fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File(["x"], "alarm.csv", { type: "text/csv" })] } });
    await fireEvent.click(screen.getByTestId("preview-button"));

    const mapping = await screen.findByTestId("mapping-editor");
    expect(within(mapping).queryByRole("textbox")).not.toBeInTheDocument();
    expect(within(mapping).getAllByRole("combobox").length).toBeGreaterThan(5);
    expect(previewBody?.get("project_id")).toBe(project.project_id);
    expect(screen.getByText("P1（紧急）")).toBeInTheDocument();
    expect(screen.getAllByText("活动报警").length).toBeGreaterThan(0);
    await fireEvent.click(screen.getByTestId("confirm-import"));
    expect(await screen.findByText(/已导入当前项目/)).toBeInTheDocument();
    expect(screen.getByTestId("onboarding-step-4")).toHaveClass("done");
  });

  it("在页面内修正异常行并随原文件重新全量预览", async () => {
    let previewCount = 0;
    let submittedCorrections: string | null = null;
    const rejected = {
      batch_id: "33333333-3333-3333-3333-333333333333", project_id: project.project_id,
      file_name: "invalid.csv", format: "CSV", status: "REJECTED", total_rows: 1,
      valid_rows: 0, error_count: 3,
      errors: [
        { source_row: 2, field: "priority", code: "INVALID_ENUM", message: "优先级无效" },
        { source_row: 2, field: "value", code: "PROJECT_RULE_RANGE", message: "值低于项目下限" },
        { source_row: 2, field: "file", code: "COLUMN_COUNT_MISMATCH", message: "数据列数与表头不一致" },
      ],
      headers: ["优先级", "位号"], mapping: { priority: "优先级", tag: "位号" }, corrections: {},
      source_rows: [{ source_row: 2, values: { 优先级: "PX", 位号: "PT-001" } }],
      preview_rows: [], created_at: "2026-08-26T08:00:00+08:00",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/health") return { ok: true, json: async () => health } as Response;
      if (url.startsWith("/api/v1/projects?")) return { ok: true, json: async () => [project] } as Response;
      if (url.endsWith("/overview")) return { ok: true, json: async () => overview } as Response;
      if (url.startsWith("/api/v1/imports?")) return { ok: true, json: async () => [] } as Response;
      if (url === "/api/v1/imports/preview") {
        previewCount += 1;
        submittedCorrections = (init?.body as FormData).get("corrections") as string | null;
        return { ok: true, json: async () => previewCount === 1 ? rejected : {
          ...rejected, status: "READY", valid_rows: 1, error_count: 0, errors: [],
          corrections: { "2": { priority: "P1" } }, preview_rows: [{ source_row: 2, tag: "PT-001", priority: "P1" }],
        } } as Response;
      }
      throw new Error(`未处理请求 ${url}`);
    }));
    render(App);
    await fireEvent.click(await screen.findByTestId("workspace-tab-import"));
    await waitFor(() => expect(screen.getByTestId("file-input")).toBeEnabled());
    await fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File(["x"], "invalid.csv", { type: "text/csv" })] } });
    await fireEvent.click(screen.getByTestId("preview-button"));

    expect(await screen.findByText("超出项目允许范围")).toBeInTheDocument();
    expect(screen.getByText("列数与表头不一致")).toBeInTheDocument();
    expect(screen.queryByText("PROJECT_RULE_RANGE")).not.toBeInTheDocument();
    expect(screen.queryByText("COLUMN_COUNT_MISMATCH")).not.toBeInTheDocument();
    const correction = await screen.findByTestId("correction-row-2-priority");
    const input = within(correction).getByLabelText("第 2 行 优先级修正值");
    expect(input).toHaveValue("PX");
    await fireEvent.update(input, "P1");
    await fireEvent.click(screen.getByRole("button", { name: "按修正值重新全量校验" }));
    expect(await screen.findByText("全文件校验通过，请核对预览后确认导入。")).toBeInTheDocument();
    expect(JSON.parse(submittedCorrections ?? "{}")).toEqual({ "2": { priority: "P1" } });
  });

  it("仅允许精确确认后删除已归档空项目", async () => {
    const archived = { ...project, status: "ARCHIVED" };
    let deleted = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.startsWith("/api/v1/projects?")) return { ok: true, json: async () => deleted ? [] : [archived] } as Response;
      if (url.endsWith("/overview")) return { ok: true, json: async () => ({ ...overview, statistics: { ...overview.statistics, batch_count: 0, alarm_count: 0 } }) } as Response;
      if (url === `/api/v1/projects/${project.project_id}` && init?.method === "DELETE") { deleted = true; return { ok: true, status: 204 } as Response; }
      throw new Error(`未处理请求 ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(ProjectWorkspace, { props: { systemAdmin: true } });

    const deletePanel = await screen.findByTestId("delete-project");
    const deleteButton = within(deletePanel).getByRole("button", { name: "删除项目" });
    expect(deleteButton).toBeDisabled();
    await fireEvent.update(within(deletePanel).getByLabelText("输入项目编号以确认删除"), project.code);
    expect(deleteButton).toBeEnabled();
    await fireEvent.click(deleteButton);
    expect(await screen.findByText(/空项目.*已删除/)).toBeInTheDocument();
    expect(screen.queryByText(project.name)).not.toBeInTheDocument();
  });

  it("有业务数据的项目归档后刷新统计且不展示删除入口", async () => {
    let archived = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/v1/projects?")) return { ok: true, json: async () => [{ ...project, status: archived ? "ARCHIVED" : "ACTIVE" }] } as Response;
      if (url.endsWith("/overview")) return { ok: true, json: async () => overview } as Response;
      if (url.endsWith("/archive")) { archived = true; return { ok: true, json: async () => ({ ...project, status: "ARCHIVED" }) } as Response; }
      throw new Error(`未处理请求 ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(ProjectWorkspace, { props: { systemAdmin: true } });
    await screen.findByText("300");
    await fireEvent.click(screen.getByRole("button", { name: "归档项目" }));
    expect(await screen.findByText("项目归档成功，只能查看历史数据。")).toBeInTheDocument();
    expect(screen.queryByTestId("delete-project")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/overview"))).toHaveLength(2);
  });

  it("演示复位后清除已删除项目并选择默认项目", async () => {
    const defaultProject = { ...project, project_id: "00000000-0000-0000-0000-000000000001", code: "DEFAULT-DEMO", name: "默认演示项目" };
    let listCount = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/v1/projects?")) {
        listCount += 1;
        return { ok: true, json: async () => listCount === 1 ? [project] : [defaultProject] } as Response;
      }
      if (url.endsWith("/overview")) return { ok: true, json: async () => ({ ...overview, project_id: url.includes(defaultProject.project_id) ? defaultProject.project_id : project.project_id }) } as Response;
      throw new Error(`未处理请求 ${url}`);
    }));
    const ResetHost = defineComponent({
      components: { ProjectWorkspace },
      setup() {
        const workspace = ref<{ resetAfterDemoReset: () => Promise<void> }>();
        return { workspace, reset: () => workspace.value?.resetAfterDemoReset() };
      },
      template: '<ProjectWorkspace ref="workspace"/><button type="button" @click="reset">模拟演示复位</button>',
    });
    render(ResetHost);
    expect(await screen.findByText(`当前：${project.name}`)).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "模拟演示复位" }));
    expect(await screen.findByText("当前：默认分析项目")).toBeInTheDocument();
    expect(screen.queryByText(`当前：${project.name}`)).not.toBeInTheDocument();
  });
});

describe("M10 人工补录和中文词典", () => {
  it("挂载和切换项目时从服务端恢复人工补录列表", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => [{
        project_id: String(input).includes("project-b") ? "project-b" : "project-a",
        batch_id: "batch-1", record_id: String(input).includes("project-b") ? "record-b" : "record-a",
        event_time: "2026-08-26T10:00:00Z", site: "厂区", area: "区域", unit: null,
        tag: String(input).includes("project-b") ? "PT-B" : "PT-A", description: "已保存补录",
        priority: "P2", state: "ACTIVE", source_system: "MANUAL_ENTRY", operator: "验收员", raw_payload: {}, invalidated_at: null,
      }],
    }) as Response);
    vi.stubGlobal("fetch", fetchMock);
    const view = render(ManualAlarmPanel, { props: { projectId: "project-a" } });
    expect(await screen.findByText("PT-A")).toBeInTheDocument();
    await view.rerender({ projectId: "project-b" });
    expect(await screen.findByText("PT-B")).toBeInTheDocument();
    expect(screen.queryByText("PT-A")).not.toBeInTheDocument();
  });

  it("完成补录、修订和作废，源操作员保留且动作身份不由客户端传递", async () => {
    let alarm = {
      project_id: project.project_id, batch_id: "batch-1", record_id: "record-1",
      event_time: "2026-08-26T10:00:00Z", site: project.site, area: project.unit_name, unit: null,
      tag: "PT-001", description: "人工补录", priority: "P2", state: "ACTIVE", source_system: "MANUAL_ENTRY",
      operator: "验收员", raw_payload: {}, invalidated_at: null,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (!init?.method && url.endsWith("/manual-alarms")) return { ok: true, json: async () => [alarm] } as Response;
      if (init?.method === "POST" && url.endsWith("/manual-alarms")) return { ok: true, json: async () => alarm } as Response;
      if (init?.method === "PATCH") { alarm = { ...alarm, description: "已修订" }; return { ok: true, json: async () => alarm } as Response; }
      if (url.endsWith("/invalidate")) { alarm = { ...alarm, invalidated_at: "2026-08-26T11:00:00Z" }; return { ok: true, json: async () => alarm } as Response; }
      throw new Error(`未处理请求 ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(ManualAlarmPanel, { props: { projectId: project.project_id, site: project.site, area: project.unit_name } });

    await fireEvent.click(screen.getByRole("button", { name: "人工补录" }));
    await fireEvent.update(screen.getByLabelText(/发生时间/), "2026-08-26T10:00");
    await fireEvent.update(screen.getByLabelText(/位号/), "PT-001");
    await fireEvent.update(screen.getByLabelText(/报警描述/), "人工补录");
    await fireEvent.update(screen.getByLabelText(/源操作员/), "验收员");
    await fireEvent.click(screen.getByRole("button", { name: "保存补录" }));
    expect(await screen.findByText(/补录成功/)).toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "编辑该补录" }));
    await fireEvent.update(screen.getByLabelText(/报警描述/), "已修订");
    await fireEvent.update(screen.getByLabelText(/修订理由/), "现场复核");
    await fireEvent.click(screen.getByRole("button", { name: "保存修订" }));
    expect(await screen.findByText(/修订已保存/)).toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "作废" }));
    await fireEvent.update(screen.getByLabelText(/作废理由/), "重复补录");
    await fireEvent.click(screen.getByRole("button", { name: "确认作废" }));
    expect((await screen.findAllByText(/已作废/)).length).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/invalidate"), expect.objectContaining({ body: JSON.stringify({ reason: "重复补录" }) }));
  });

  it("机器枚举、优先级和审计详情均转换为业务中文", () => {
    expect(zh("IN_PROGRESS")).toBe("处理中");
    expect(zh("EQUIPMENT_FAULT")).toBe("设备故障");
    expect(priorityLabel("P1")).toBe("P1（紧急）");
    expect(auditDetails({ from_status: "OPEN", to_status: "CLOSED", reason: "复核" })).toBe("原状态：待处理；新状态：已关闭；原因：复核");
    expect(localizedEvidence("NORMAL / EQUIPMENT_FAULT，状态 ACTIVE")).toBe("一般报警 / 设备故障，状态 活动报警");
    expect(localizedEvidence("NORMALIZED 与 PRE_ACTIVE_CODE 保持原文")).toBe("NORMALIZED 与 PRE_ACTIVE_CODE 保持原文");
    expect([
      "PROJECT_RULE_REQUIRED", "PROJECT_RULE_RANGE", "VALUE_TOO_LONG",
      "COLUMN_COUNT_MISMATCH", "DUPLICATE_HEADER", "INVALID_MAPPING",
    ].map(zh)).toEqual([
      "不符合项目必填规则", "超出项目允许范围", "内容长度超限",
      "列数与表头不一致", "表头名称重复", "字段映射无效",
    ]);
  });

  it("审计筛选和表格只显示中文事件及结果", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      page: 0, size: 50, total: 1, items: [{
        event_id: "event-1", event_type: "RESULT_OVERRIDDEN", occurred_at: "2026-08-26T10:00:00+08:00",
        operator: "审核员", target_type: "ALARM_RECORD", target_id: "record-1", result: "SUCCESS", trace_id: "trace-1", details: { reason: "复核" },
      }],
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
    render(ReviewOperations, { props: { projectId: project.project_id, canManage: true, systemAdmin: true } });
    await fireEvent.click(screen.getByTestId("audit-refresh"));
    const table = await screen.findByTestId("audit-table");
    expect(table).toHaveTextContent("人工修订分类");
    expect(table).toHaveTextContent("报警记录");
    expect(table).toHaveTextContent("成功");
    expect(table).not.toHaveTextContent("RESULT_OVERRIDDEN");
  });
});
