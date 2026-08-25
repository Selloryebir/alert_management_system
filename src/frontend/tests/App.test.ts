import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/vue";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "../src/App.vue";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("M1 状态页", () => {
  it("显示 Demo 身份、合成数据标识和三个健康组件", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "UP",
        service: "alert-management-backend",
        version: "0.1.0",
        identity: "2026 年灾后重建 Demo",
        components: {
          system: { status: "UP" },
          database: { status: "UP" },
          algorithm: { status: "UP" },
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(App);

    expect(
      screen.getByRole("heading", { name: "2026 年灾后重建 Demo" }),
    ).toBeInTheDocument();
    expect(screen.getByText("仅使用合成数据")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByText("所有基础服务正常")).toBeInTheDocument();
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/health", {
      headers: { Accept: "application/json" },
    });
    for (const label of ["主系统", "PostgreSQL", "算法服务"]) {
      const card = screen.getByRole("heading", { name: label }).closest("article");
      expect(card).not.toBeNull();
      expect(within(card as HTMLElement).getByText("UP")).toBeInTheDocument();
    }
  });

  it("请求失败时保留 UNKNOWN 并给出可行动提示", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));

    render(App);

    expect(
      await screen.findByText(
        "无法访问主系统健康接口。请确认主系统已启动，然后点击“重新检查”。",
      ),
    ).toHaveAttribute("role", "alert");
    expect(screen.getAllByText("UNKNOWN")).toHaveLength(3);
    expect(screen.getByRole("button", { name: "重新检查" })).toBeEnabled();
  });

  it("DOWN 提供操作提示且缺失状态不推断为成功", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          status: "DEGRADED",
          components: {
            system: { status: "UP" },
            database: { status: "DOWN", detail: "connection refused" },
          },
        }),
      }),
    );

    render(App);

    await waitFor(() => {
      expect(
        screen.getByLabelText("PostgreSQL状态 DOWN"),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("部分服务不可用")).toBeInTheDocument();
    expect(
      screen.getByText(/请确认 PostgreSQL 进程已启动且连接配置正确/),
    ).toBeInTheDocument();
    expect(screen.getAllByText("UNKNOWN")).toHaveLength(1);
  });
});

describe("M2 导入向导", () => {
  const healthPayload = {
    components: {
      system: { status: "UP" },
      database: { status: "UP" },
      algorithm: { status: "UP" },
    },
  };

  const readyBatch = {
    batch_id: "7b39d4bd-80fe-4db3-962f-fad54cc93c4f",
    file_name: "alarm.csv",
    format: "CSV",
    status: "READY",
    total_rows: 1,
    valid_rows: 1,
    error_count: 0,
    errors: [],
    preview_rows: [
      {
        source_row: 2,
        event_time: "2026-01-15T08:00:00+08:00",
        tag: "PT-001",
        description: "压力高",
        priority: "P1",
        state: "ACTIVE",
      },
    ],
    created_at: "2026-08-25T10:00:00+08:00",
  };

  it("选择文件后完成预览和确认导入", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/health") {
        return { ok: true, json: async () => healthPayload } as Response;
      }
      if (url === "/api/v1/imports/preview") {
        expect(init?.method).toBe("POST");
        expect(init?.body).toBeInstanceOf(FormData);
        return { ok: true, json: async () => readyBatch } as Response;
      }
      if (url.endsWith("/confirm")) {
        return {
          ok: true,
          json: async () => ({ ...readyBatch, status: "IMPORTED" }),
        } as Response;
      }
      throw new Error(`未处理请求 ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(App);
    const fileInput = screen.getByLabelText("报警文件");
    await fireEvent.change(fileInput, {
      target: {
        files: [new File(["header\nvalue"], "alarm.csv", { type: "text/csv" })],
      },
    });
    await fireEvent.click(screen.getByRole("button", { name: "校验并预览" }));

    expect(await screen.findByText("alarm.csv")).toBeInTheDocument();
    expect(screen.getByText("PT-001")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "确认导入" }));
    expect(await screen.findByText(/已导入/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认导入" })).not.toBeInTheDocument();
  });

  it("展示精确校验错误并可刷新最近批次", async () => {
    const rejectedBatch = {
      ...readyBatch,
      batch_id: "9e329970-7b61-46ad-bab1-c1445368701d",
      file_name: "invalid.csv",
      status: "REJECTED",
      valid_rows: 0,
      error_count: 1,
      errors: [
        {
          source_row: 2,
          field: "priority",
          code: "INVALID_ENUM",
          message: "优先级必须为 P1、P2、P3 或 P4",
        },
      ],
      preview_rows: [],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/health") {
          return { ok: true, json: async () => healthPayload } as Response;
        }
        if (url === "/api/v1/imports/preview") {
          return { ok: true, json: async () => rejectedBatch } as Response;
        }
        if (url === "/api/v1/imports?limit=20") {
          return { ok: true, json: async () => [rejectedBatch] } as Response;
        }
        throw new Error(`未处理请求 ${url}`);
      }),
    );

    render(App);
    await fireEvent.change(
      screen.getByLabelText("报警文件"),
      {
        target: {
          files: [new File(["invalid"], "invalid.csv", { type: "text/csv" })],
        },
      },
    );
    await fireEvent.click(screen.getByRole("button", { name: "校验并预览" }));
    expect(await screen.findByText("INVALID_ENUM")).toBeInTheDocument();
    expect(screen.getByText("priority")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认导入" })).not.toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "刷新批次" }));
    const table = await screen.findByText("最近导入批次");
    expect(table.closest("table")).toHaveTextContent("invalid.csv");
  });
});

describe("M4 浏览器业务闭环", () => {
  const healthPayload = {
    components: {
      system: { status: "UP" },
      database: { status: "UP" },
      algorithm: { status: "UP" },
    },
  };
  const batchId = "7b39d4bd-80fe-4db3-962f-fad54cc93c4f";
  const runId = "63c933ab-6078-4a02-bce2-2f3fdb45d4aa";
  const recordId = "9310cba7-8351-4317-807e-a5d29bf5ff62";
  const readyBatch = {
    batch_id: batchId,
    file_name: "synthetic_smoke.csv",
    format: "CSV",
    status: "READY",
    total_rows: 300,
    valid_rows: 300,
    error_count: 0,
    errors: [],
    preview_rows: [],
    created_at: "2026-08-25T10:00:00+08:00",
  };
  const completedRun = {
    run_id: runId,
    batch_id: batchId,
    attempt: 1,
    status: "COMPLETED",
    contract_version: "v1",
    algorithm_version: "0.1.0",
    rule_version: "rules-v1.0.0",
    parameters: {},
    summary: {
      input_count: 300,
      success_count: 300,
      failure_count: 0,
      noise_type_counts: { NORMAL: 170, DUPLICATE: 30, CHATTER: 40, SHORT_LIVED: 30, PERSISTENT: 30 },
      cause_category_counts: { PROCESS_DISTURBANCE: 90, EQUIPMENT_FAULT: 30, INSTRUMENT_ISSUE: 30, MAINTENANCE_TEST: 20, UNKNOWN: 130 },
      event_chain_count: 12,
    },
    started_at: "2026-08-25T10:01:00+08:00",
    completed_at: "2026-08-25T10:01:01+08:00",
  };
  const alarmItem = {
    record_id: recordId,
    source_row: 222,
    event_time: "2026-01-15T09:40:20+08:00",
    site: "SYNTHETIC_SITE_01",
    area: "SYNTHETIC_AREA_02",
    unit: "SYNTHETIC_UNIT_06",
    tag: "SYNTHETIC-EQUIPMENT_TRIP-001",
    description: "[SYNTHETIC] 设备跳停序列步骤 1",
    priority: "P2",
    alarm_state: "ACTIVE",
    noise_type: "NORMAL",
    alarm_class: "STANDARD",
    cause_category: "EQUIPMENT_FAULT",
    score: 0.5,
    disposition_status: "OPEN",
  };

  it("完成导入、分析、看板筛选、详情及处理关闭主路径", async () => {
    let disposition = "OPEN";
    const history: Array<Record<string, string>> = [];
    const detail = () => ({
      ...alarmItem,
      disposition_status: disposition,
      raw_payload: { source_row: "222", tag: alarmItem.tag },
      evidence: ["命中 EQUIPMENT_TRIP_SEQUENCE 关联事件链规则。"],
      disposition: { status: disposition, operator: history.at(-1)?.operator },
      disposition_history: [...history],
      event_chains: [
        {
          chain_id: "chain-1",
          start_time: alarmItem.event_time,
          end_time: "2026-01-15T09:41:04+08:00",
          association_rule: "EQUIPMENT_TRIP_SEQUENCE",
          explanation: "五步报警按顺序出现；这是关联建议，不代表已确认根因。",
          members: [
            { record_id: recordId, source_row: 222, order: 0 },
            { record_id: "record-223", source_row: 223, order: 1 },
          ],
        },
      ],
    });
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/health") return { ok: true, json: async () => healthPayload } as Response;
      if (url === "/api/v1/imports/preview") return { ok: true, json: async () => readyBatch } as Response;
      if (url.endsWith("/confirm")) return { ok: true, json: async () => ({ ...readyBatch, status: "IMPORTED" }) } as Response;
      if (url === `/api/v1/imports/${batchId}/analyses`) return { ok: true, json: async () => completedRun } as Response;
      if (url === `/api/v1/analyses/${runId}/dashboard`) {
        return {
          ok: true,
          json: async () => ({
            run_id: runId,
            batch_id: batchId,
            total: 300,
            disposition_counts: { OPEN: 300, IN_PROGRESS: 0, CLOSED: 0 },
            trend: [{ bucket: "2026-01-15T09:00:00+08:00", count: 80 }],
            priority_counts: { P1: 75, P2: 75, P3: 75, P4: 75 },
            area_counts: { SYNTHETIC_AREA_02: 80 },
            unit_counts: { SYNTHETIC_UNIT_06: 30 },
            noise_type_counts: completedRun.summary.noise_type_counts,
            cause_category_counts: completedRun.summary.cause_category_counts,
          }),
        } as Response;
      }
      if (url.startsWith(`/api/v1/analyses/${runId}/alarms?`)) {
        return { ok: true, json: async () => ({ items: [{ ...alarmItem, disposition_status: disposition }], total: 1, page: 0, size: 20 }) } as Response;
      }
      if (url.endsWith(`/${recordId}/disposition`)) {
        const body = JSON.parse(String(init?.body)) as { status: string; operator: string; note: string };
        history.push({
          from_status: disposition,
          to_status: body.status,
          operator: body.operator,
          note: body.note,
          occurred_at: `2026-08-25T10:02:0${history.length}+08:00`,
        });
        disposition = body.status;
        return { ok: true, json: async () => ({ status: disposition, operator: body.operator, note: body.note }) } as Response;
      }
      if (url === `/api/v1/analyses/${runId}/alarms/${recordId}`) {
        return { ok: true, json: async () => detail() } as Response;
      }
      throw new Error(`未处理请求 ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(App);
    await fireEvent.change(screen.getByTestId("file-input"), {
      target: { files: [new File(["synthetic"], "synthetic_smoke.csv", { type: "text/csv" })] },
    });
    await fireEvent.click(screen.getByTestId("preview-button"));
    await fireEvent.click(await screen.findByTestId("confirm-import"));
    await fireEvent.click(await screen.findByTestId("start-analysis"));

    expect(await screen.findByTestId("dashboard-total")).toHaveTextContent("300");
    expect(screen.getByTestId("dashboard-chains")).toHaveTextContent("12");
    expect(screen.getByTestId("dashboard-noise-DUPLICATE")).toHaveTextContent("30");
    expect(screen.getByTestId("dashboard-cause-EQUIPMENT_FAULT")).toHaveTextContent("30");

    await fireEvent.update(screen.getByTestId("filter-cause"), "EQUIPMENT_FAULT");
    await fireEvent.click(screen.getByRole("button", { name: "应用筛选" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("cause_category=EQUIPMENT_FAULT"),
        expect.anything(),
      );
    });
    await fireEvent.click(await screen.findByTestId("alarm-row-222"));

    expect(await screen.findByTestId("alarm-detail")).toHaveTextContent(alarmItem.tag);
    expect(screen.getByTestId("detail-source-row")).toHaveTextContent("222");
    expect(screen.getByTestId("detail-evidence")).toHaveTextContent("关联事件链规则");
    expect(screen.getByTestId("detail-event-chains")).toHaveTextContent("关联建议，不代表已确认根因");

    await fireEvent.update(screen.getByTestId("disposition-operator"), "审核员A");
    await fireEvent.click(screen.getByTestId("disposition-start"));
    expect(await screen.findByTestId("service-error")).toHaveTextContent("请填写处置说明");
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/disposition")),
    ).toHaveLength(0);
    await fireEvent.update(screen.getByTestId("disposition-note"), "开始核查");
    await fireEvent.click(screen.getByTestId("disposition-start"));
    await waitFor(() => {
      expect(screen.getByTestId("disposition-close")).toBeEnabled();
    });
    expect(screen.getByTestId("disposition-reopen")).toBeEnabled();

    await fireEvent.update(screen.getByTestId("disposition-note"), "已完成合成报警处置");
    await fireEvent.click(screen.getByTestId("disposition-close"));
    await waitFor(() => {
      expect(screen.getByTestId("alarm-detail")).toHaveTextContent("CLOSED");
    });
    expect(screen.getByTestId("disposition-history")).toHaveTextContent("OPEN → IN_PROGRESS");
    expect(screen.getByTestId("disposition-history")).toHaveTextContent("IN_PROGRESS → CLOSED");
  });

  it("空状态与算法服务失败都给出可行动中文提示", async () => {
    const importedBatch = { ...readyBatch, status: "IMPORTED" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/health") return { ok: true, json: async () => healthPayload } as Response;
        if (url === "/api/v1/imports?limit=20") return { ok: true, json: async () => [importedBatch] } as Response;
        if (url === `/api/v1/imports/${batchId}/analyses`) {
          return {
            ok: true,
            json: async () => ({ ...completedRun, status: "FAILED", failure: "算法服务不可用" }),
          } as Response;
        }
        throw new Error(`未处理请求 ${url}`);
      }),
    );

    render(App);
    expect(screen.getByTestId("empty-state")).toHaveTextContent("尚无可分析批次");
    await fireEvent.click(screen.getByRole("button", { name: "刷新批次" }));
    await fireEvent.click(await screen.findByRole("button", { name: "开始分析" }));

    expect(await screen.findByTestId("service-error")).toHaveTextContent("算法服务不可用");
    expect(screen.getByTestId("service-error")).toHaveTextContent("恢复后重试");
  });

  it("可从最近完成批次查看分析并呈现业务空数据", async () => {
    const completedBatch = { ...readyBatch, status: "COMPLETED" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/health") return { ok: true, json: async () => healthPayload } as Response;
        if (url === "/api/v1/imports?limit=20") return { ok: true, json: async () => [completedBatch] } as Response;
        if (url === `/api/v1/imports/${batchId}/analyses/latest`) {
          return { ok: true, json: async () => ({ ...completedRun, summary: { ...completedRun.summary, input_count: 0, success_count: 0, event_chain_count: 0 } }) } as Response;
        }
        if (url === `/api/v1/analyses/${runId}/dashboard`) {
          return {
            ok: true,
            json: async () => ({
              run_id: runId,
              batch_id: batchId,
              total: 0,
              disposition_counts: { OPEN: 0, IN_PROGRESS: 0, CLOSED: 0 },
              trend: [],
              priority_counts: {},
              area_counts: {},
              unit_counts: {},
              noise_type_counts: {},
              cause_category_counts: {},
            }),
          } as Response;
        }
        if (url.startsWith(`/api/v1/analyses/${runId}/alarms?`)) {
          return { ok: true, json: async () => ({ items: [], total: 0, page: 0, size: 20 }) } as Response;
        }
        throw new Error(`未处理请求 ${url}`);
      }),
    );

    render(App);
    await fireEvent.click(screen.getByRole("button", { name: "刷新批次" }));
    await fireEvent.click(await screen.findByRole("button", { name: "查看分析" }));

    expect(await screen.findByTestId("dashboard-total")).toHaveTextContent("0");
    expect(screen.getByText("当前条件下没有报警记录，请清空或调整筛选。")).toBeInTheDocument();
    expect(screen.getByText("暂无趋势数据。")).toBeInTheDocument();
  });
});
