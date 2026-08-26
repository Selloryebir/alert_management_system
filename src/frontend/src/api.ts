export interface ApiErrorPayload {
  code?: string;
  message?: string;
  failure?: string;
  trace_id?: string;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code?: string,
    public readonly traceId?: string,
    message = `请求失败（HTTP ${status}）`,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface CsrfToken {
  token: string;
  header_name: string;
  parameter_name: string;
}

let csrfToken: CsrfToken | undefined;
let unauthorizedHandler: (() => void) | undefined;

export function setCsrfToken(value?: CsrfToken): void {
  csrfToken = value;
}

export function setUnauthorizedHandler(handler?: () => void): void {
  unauthorizedHandler = handler;
}

function isUnsafe(method: string): boolean {
  return !["GET", "HEAD", "OPTIONS", "TRACE"].includes(method.toUpperCase());
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const method = init.method ?? "GET";
  const headers = new Headers(init.headers);
  if (isUnsafe(method)) {
    if (!csrfToken) throw new Error("安全令牌尚未初始化，请刷新页面后重试。");
    headers.set(csrfToken.header_name, csrfToken.token);
  }
  const response = await fetch(input, { ...init, credentials: "same-origin", headers });
  if (response.status === 401) unauthorizedHandler?.();
  return response;
}

export async function apiError(response: Response): Promise<ApiError> {
  let payload: ApiErrorPayload = {};
  try {
    payload = (await response.json()) as ApiErrorPayload;
  } catch {
    // 非 JSON 错误仍保留 HTTP 状态，避免把解析异常伪装成业务错误。
  }
  const fallback = response.status === 401
    ? "登录已失效，请重新登录。"
    : response.status === 403
      ? "当前账号无权执行该操作。"
      : `请求失败（HTTP ${response.status}）`;
  return new ApiError(
    response.status,
    payload.code,
    payload.trace_id,
    payload.message || payload.failure || fallback,
  );
}

export async function apiJson<T>(response: Response): Promise<T> {
  if (!response.ok) throw await apiError(response);
  return (await response.json()) as T;
}

export async function requireOk(response: Response): Promise<Response> {
  if (!response.ok) throw await apiError(response);
  return response;
}
