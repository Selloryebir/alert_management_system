import type { CountPoint, Dashboard } from "./business";

export interface ChartPoint extends CountPoint {
  x: number;
  y: number;
}

export interface DonutSegment {
  label: string;
  count: number;
  percentage: number;
  offset: number;
  color: string;
}

const DONUT_COLORS = ["#0f5b66", "#d26a33", "#577590", "#2f7d4a", "#8a4f7d"];

export function trendChartPoints(
  trend: CountPoint[],
  width = 600,
  height = 220,
  padding = 32,
): ChartPoint[] {
  if (trend.length === 0) return [];
  const maximum = Math.max(...trend.map((point) => point.count), 1);
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;
  return trend.map((point, index) => ({
    ...point,
    x: padding + (trend.length === 1 ? chartWidth / 2 : (index / (trend.length - 1)) * chartWidth),
    y: padding + chartHeight - (point.count / maximum) * chartHeight,
  }));
}

export function donutSegments(values: Record<string, number>): DonutSegment[] {
  const items = Object.entries(values).filter(([, count]) => count > 0);
  const total = items.reduce((sum, [, count]) => sum + count, 0);
  let offset = 0;
  return items.map(([label, count], index) => {
    const percentage = total > 0 ? (count / total) * 100 : 0;
    const segment = { label, count, percentage, offset, color: DONUT_COLORS[index % DONUT_COLORS.length] };
    offset += percentage;
    return segment;
  });
}

export function dashboardCsvRows(dashboard: Dashboard, eventChainCount: number): string[][] {
  const sorted = (values: Record<string, number>) =>
    Object.entries(values).sort(([, first], [, second]) => second - first);
  return [
    ["统计维度", "分类", "数量"],
    ["总览", "报警总数", String(dashboard.total)],
    ["处置状态", "待处理", String(dashboard.disposition_counts.OPEN ?? 0)],
    ["处置状态", "处理中", String(dashboard.disposition_counts.IN_PROGRESS ?? 0)],
    ["处置状态", "已关闭", String(dashboard.disposition_counts.CLOSED ?? 0)],
    ["总览", "关联事件链", String(eventChainCount)],
    ...dashboard.trend.map((point) => ["小时趋势", point.bucket, String(point.count)]),
    ...sorted(dashboard.priority_counts).map(([label, count]) => ["优先级", label, String(count)]),
    ...sorted(dashboard.area_counts).map(([label, count]) => ["区域分布", label, String(count)]),
    ...sorted(dashboard.unit_counts).map(([label, count]) => ["单元分布", label, String(count)]),
    ...sorted(dashboard.noise_type_counts).map(([label, count]) => ["报警类型", label, String(count)]),
    ...sorted(dashboard.cause_category_counts).map(([label, count]) => ["原因建议", label, String(count)]),
  ];
}

export function encodeCsv(rows: string[][]): string {
  return rows
    .map((row) => row.map((cell) => `"${cell.replaceAll('"', '""')}"`).join(","))
    .join("\r\n");
}
