import { describe, expect, it } from "vitest";

import { dashboardCsvRows, donutSegments, encodeCsv, trendChartPoints } from "../src/dashboardPresentation";
import type { Dashboard } from "../src/business";

const dashboard: Dashboard = {
  run_id: "run-1",
  batch_id: "batch-1",
  total: 4,
  disposition_counts: { OPEN: 2, IN_PROGRESS: 1, CLOSED: 1 },
  trend: [{ bucket: "08:00", count: 1 }, { bucket: "09:00", count: 4 }],
  priority_counts: { P1: 1, P2: 3 },
  area_counts: { 一区: 4 },
  unit_counts: { 一单元: 4 },
  noise_type_counts: { NORMAL: 3, CHATTER: 1 },
  cause_category_counts: { UNKNOWN: 4 },
};

describe("看板展示数据", () => {
  it("按趋势峰值生成真实时序坐标，并处理单点和空数据", () => {
    const points = trendChartPoints(dashboard.trend);
    expect(points[1].y).toBe(32);
    expect(points[0].y).toBeGreaterThan(points[1].y);
    expect(trendChartPoints([{ bucket: "08:00", count: 2 }])[0].x).toBe(300);
    expect(trendChartPoints([])).toEqual([]);
  });

  it("环图比例来自互斥报警类型实际计数", () => {
    const segments = donutSegments(dashboard.noise_type_counts);
    expect(segments.map(({ label, count, percentage }) => ({ label, count, percentage }))).toEqual([
      { label: "NORMAL", count: 3, percentage: 75 },
      { label: "CHATTER", count: 1, percentage: 25 },
    ]);
    expect(segments[1].offset).toBe(75);
  });

  it("CSV 包含总览、处置、事件链、趋势和全部分布并正确转义", () => {
    const csv = encodeCsv(dashboardCsvRows(dashboard, 2));
    for (const expected of ["报警总数", "待处理", "处理中", "已关闭", "关联事件链", "小时趋势", "报警类型", "原因建议"]) {
      expect(csv).toContain(expected);
    }
    expect(encodeCsv([["字段", "含\"引号"]])).toContain('"含""引号"');
  });
});
