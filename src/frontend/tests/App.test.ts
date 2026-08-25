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
