const labels: Record<string, string> = {
  UP: "正常",
  DOWN: "不可用",
  UNKNOWN: "未知",
  READY: "校验通过",
  REJECTED: "校验未通过",
  IMPORTED: "已导入",
  ANALYZING: "分析中",
  COMPLETED: "已完成",
  FAILED: "失败",
  ACTIVE: "活动报警",
  RETURNED: "已恢复",
  ACKNOWLEDGED: "已确认",
  OPEN: "待处理",
  IN_PROGRESS: "处理中",
  CLOSED: "已关闭",
  ARCHIVED: "已归档",
  NORMAL: "一般报警",
  DUPLICATE: "重复报警",
  CHATTER: "抖动报警",
  SHORT_LIVED: "短时报警",
  PERSISTENT: "持续报警",
  NUISANCE: "干扰报警",
  ACTIONABLE: "需行动报警",
  STANDARD: "标准报警",
  PROCESS_DISTURBANCE: "工艺扰动",
  EQUIPMENT_FAULT: "设备故障",
  INSTRUMENT_ISSUE: "仪表问题",
  MAINTENANCE_TEST: "维护测试",
  SUCCESS: "成功",
  ERROR: "失败",
  IMPORT_CREATED: "创建导入批次",
  IMPORT_REJECTED: "导入校验未通过",
  IMPORT_CONFIRMED: "确认导入",
  ANALYSIS_STARTED: "开始分析",
  ANALYSIS_COMPLETED: "分析完成",
  ANALYSIS_FAILED: "分析失败",
  RESULT_OVERRIDDEN: "人工修订分类",
  DISPOSITION_CHANGED: "更新处置状态",
  REPORT_EXPORTED: "导出报告",
  PROJECT_CREATED: "创建项目",
  PROJECT_UPDATED: "更新项目",
  PROJECT_ARCHIVED: "归档项目",
  PROJECT_RESTORED: "恢复项目",
  PROJECT_DELETED: "删除空项目",
  MANUAL_ALARM_CREATED: "人工补录报警",
  MANUAL_ALARM_UPDATED: "修订人工补录",
  MANUAL_ALARM_INVALIDATED: "作废人工补录",
  PROJECT: "项目",
  IMPORT_BATCH: "导入批次",
  ANALYSIS_RUN: "分析运行",
  ALARM_RECORD: "报警记录",
  REPORT: "报告",
  MISSING_HEADER: "缺少表头",
  REQUIRED_VALUE_MISSING: "必填值缺失",
  INVALID_TIME: "时间格式错误",
  INVALID_ENUM: "选项值无效",
  INVALID_NUMBER: "数字格式错误",
  TIME_ORDER_INVALID: "时间顺序错误",
  DUPLICATE_SOURCE_ROW: "源行重复",
  PROJECT_RULE_REQUIRED: "不符合项目必填规则",
  PROJECT_RULE_RANGE: "超出项目允许范围",
  VALUE_TOO_LONG: "内容长度超限",
  COLUMN_COUNT_MISMATCH: "列数与表头不一致",
  DUPLICATE_HEADER: "表头名称重复",
  INVALID_MAPPING: "字段映射无效",
  CSV: "CSV 表格",
  TXT: "制表符文本",
  XLSX: "Excel 工作簿",
  event_time: "发生时间",
  return_time: "恢复时间",
  ack_time: "确认时间",
  site: "厂区",
  area: "装置或区域",
  unit: "工艺单元",
  tag: "报警位号",
  description: "报警描述",
  priority: "优先级",
  state: "报警状态",
  value: "当时值",
  threshold: "报警阈值",
  engineering_unit: "工程单位",
  source_system: "来源系统",
  operator: "源操作员",
};

export function zh(value: string | null | undefined): string {
  if (!value) return "未提供";
  return labels[value] ?? value;
}

export function fieldLabel(value: string): string {
  return labels[value] ?? value;
}

export function priorityLabel(value: string): string {
  const labelsByPriority: Record<string, string> = {
    P1: "P1（紧急）",
    P2: "P2（高）",
    P3: "P3（中）",
    P4: "P4（低）",
  };
  return labelsByPriority[value] ?? value;
}

export function projectStatusLabel(value: string): string {
  return value === "ACTIVE" ? "使用中" : value === "ARCHIVED" ? "已归档" : value;
}

const evidenceValues = [
  "PROCESS_DISTURBANCE", "EQUIPMENT_FAULT", "INSTRUMENT_ISSUE", "MAINTENANCE_TEST",
  "ACKNOWLEDGED", "SHORT_LIVED", "IN_PROGRESS", "PERSISTENT", "ACTIONABLE",
  "DUPLICATE", "NUISANCE", "STANDARD", "RETURNED", "CHATTER", "NORMAL",
  "UNKNOWN", "ACTIVE", "CLOSED", "OPEN",
];

export function localizedEvidence(text: string): string {
  return evidenceValues.reduce((result, value) => result.replace(
    new RegExp(`(?<![A-Z0-9_])${value}(?![A-Z0-9_])`, "g"),
    labels[value],
  ), text);
}

const detailLabels: Record<string, string> = {
  reason: "原因",
  status: "状态",
  from_status: "原状态",
  to_status: "新状态",
  format: "格式",
  file_name: "文件名",
  record_id: "报警记录",
  run_id: "分析运行",
  batch_id: "导入批次",
  operator: "操作者",
  report_title: "报告抬头",
  project_id: "项目",
};

export function auditDetails(details: Record<string, unknown>): string {
  const entries = Object.entries(details);
  if (entries.length === 0) return "无补充说明";
  return entries
    .map(([key, value]) => {
      const rendered = typeof value === "string" ? zh(value) : String(value);
      return `${detailLabels[key] ?? fieldLabel(key)}：${rendered}`;
    })
    .join("；");
}
