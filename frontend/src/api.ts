async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status}`;
    try {
      const body = await res.json();
      msg = body.detail || JSON.stringify(body);
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

export const api = {
  get: <T>(path: string) => fetch(path).then((r) => handle<T>(r)),
  post: <T>(path: string, body?: unknown) =>
    fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then((r) => handle<T>(r)),
  put: <T>(path: string, body: unknown) =>
    fetch(path, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => handle<T>(r)),
  patch: <T>(path: string, body: unknown) =>
    fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => handle<T>(r)),
  del: <T>(path: string) => fetch(path, { method: "DELETE" }).then((r) => handle<T>(r)),
};

export const fmt = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });

export const fmtInt = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : v.toLocaleString("en-IN");
