import { cleanup, render, screen, waitFor, within } from "@testing-library/vue";
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
