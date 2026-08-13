import { createClient } from "@/lib/supabase/client";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Base URL for WebSocket endpoints (http→ws, https→wss). */
export const API_WS_URL = API_URL.replace(/^http/, "ws");

/** Error thrown by apiCall — carries the HTTP status so callers can branch
 * (e.g. 409 = host key changed / save conflict).
 *
 * `detail` keeps FastAPI's raw `detail` payload. Some endpoints answer with a
 * structured one — US-26.1's preflight returns a list of named checks, US-26.7's
 * capacity guard returns its reasons — and coercing that into `message` would
 * hand the UI "[object Object]" instead of what actually failed. */
export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(message: unknown, status: number) {
    super(typeof message === "string" ? message : JSON.stringify(message));
    this.status = status;
    this.detail = message;
  }
}

/** US-79.4 (prod BUG-5): a fetch that never reached the API at all — the
 * network dropped, not the server refusing. The browser's own message
 * ("Load failed", "Failed to fetch") names nothing; this carries the request
 * so a toast or an error report can say which call failed. */
export class NetworkError extends Error {
  request: string;
  constructor(cause: unknown, method: string, path: string) {
    super(`could not reach the API (${method} ${path})`);
    this.name = "NetworkError";
    this.request = `${method} ${path}`;
    this.cause = cause;
  }
}

/** Every API fetch leaves through here so a transport rejection is typed. */
async function fetchApi(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_URL}${path}`, init);
  } catch (cause) {
    throw new NetworkError(cause, (init.method ?? "GET").toUpperCase(), path);
  }
}

/** Like apiFetch, but throws ApiError (with .status) on failure. */
export async function apiCall(path: string, init?: RequestInit) {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) throw new ApiError("Not signed in", 401);

  // A JSON body needs its content type or FastAPI cannot parse it and answers
  // "Input should be a valid dictionary" about a body that is perfectly valid.
  // Every existing caller passed the header by hand; one that forgot found out
  // in production. Set here so the next one cannot.
  const jsonBody = typeof init?.body === "string";
  const resp = await fetchApi(path, {
    ...init,
    headers: {
      ...(jsonBody ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
      Authorization: `Bearer ${session.access_token}`,
    },
  });
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    throw new ApiError(body?.detail ?? `API error ${resp.status}`, resp.status);
  }
  return body;
}

/** Fetch the current Supabase access token (for WebSocket handshakes). */
export async function getAccessToken(): Promise<string> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) throw new Error("Not signed in");
  return session.access_token;
}

/** Call the FastAPI backend with the current Supabase session JWT. */
export async function apiFetch(path: string, init?: RequestInit) {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    throw new Error("Not signed in");
  }

  const resp = await fetchApi(path, {
    ...init,
    headers: {
      ...init?.headers,
      Authorization: `Bearer ${session.access_token}`,
    },
  });

  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    throw new Error(body?.detail ?? `API error ${resp.status}`);
  }
  return body;
}

/** Like apiFetch, but for endpoints that return plain text (e.g. markdown)
 * rather than JSON. */
export async function apiFetchText(path: string, init?: RequestInit) {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    throw new Error("Not signed in");
  }

  const resp = await fetchApi(path, {
    ...init,
    headers: {
      ...init?.headers,
      Authorization: `Bearer ${session.access_token}`,
    },
  });

  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(detail || `API error ${resp.status}`);
  }
  return resp.text();
}
