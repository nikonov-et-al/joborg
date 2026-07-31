const TOKEN_HEADER = "X-Jobtracker-Token";

function errorFor(response) {
  return response.text().then(message => {
    try { message = JSON.parse(message).detail || message; } catch {}
    throw new Error(message || `${response.status} ${response.statusText}`);
  });
}

function authHeaders(headers, token, body, accept) {
  const result = new Headers(headers || {});
  result.delete(TOKEN_HEADER);
  if (token) result.set(TOKEN_HEADER, token);
  if (accept && !result.has("Accept")) result.set("Accept", accept);
  if (body instanceof FormData) result.delete("Content-Type");
  else if (body != null && !result.has("Content-Type")) result.set("Content-Type", "application/json");
  return result;
}

export function createApiClient({ api = "/api", token = "", fetchFn = fetch } = {}) {
  async function send(path, options = {}, accept = "application/json") {
    const { headers, body, ...rest } = options;
    const response = await fetchFn(`${api}${path}`, { ...rest, body, headers: authHeaders(headers, token, body, accept) });
    if (!response.ok) await errorFor(response);
    return response;
  }

  return {
    async request(path, options) { return (await send(path, options)).json(); },
    async download(path, filename, options) {
      const blob = await (await send(path, options, "application/pdf")).blob();
      const url = URL.createObjectURL(blob);
      const anchor = Object.assign(document.createElement("a"), { href: url, download: filename });
      document.body.append(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
    },
  };
}

export function tokenFromLocation(locationRef = location, historyRef = history) {
  const url = new URL(locationRef.href);
  const token = url.searchParams.get("token") || "";
  if (url.searchParams.has("token")) {
    url.searchParams.delete("token");
    historyRef.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }
  return token;
}
