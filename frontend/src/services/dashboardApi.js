const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api").replace(/\/$/, "");

async function request(path, options = {}) {
  const url = `${API_BASE_URL}${path}`;
  const method = options.method ?? "GET";
  let response;

  try {
    response = await fetch(url, {
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
      ...options,
    });
  } catch (error) {
    console.error("[AIMS API] Request failed before response", { method, url, error });
    throw new Error(
      `AIMS API is unreachable for ${method} ${url}. Verify the backend health endpoint at ${API_BASE_URL.replace(/\/api$/, "")}/api/health.`
    );
  }

  if (!response.ok) {
    const errorBody = await response.json().catch(() => null);
    const message = errorBody?.error ?? `API request failed with status ${response.status}`;
    console.error("[AIMS API] Request returned an error", { method, url, status: response.status, message });
    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined;
  }

  return response.json();
}

function withAgentQuery(path, agentId) {
  const params = new URLSearchParams({ agentId: String(agentId) });
  return `${path}?${params.toString()}`;
}

export function listAgents() {
  return request("/agents");
}

export async function getReceivedEmails() {
  return request("/emails/received");
}

export function getProcessedEmails() {
  return request("/emails/processed");
}

export function getClientEmailSummary() {
  return request("/emails/client-summary");
}

export function getEmailsByCustomer(customerName) {
  const params = new URLSearchParams({ customerName });
  return request(`/emails/by-customer?${params.toString()}`);
}

export function getPriorityTypeSummary() {
  return request("/emails/priority-type-summary");
}

export function getEmailsByPriorityType(type) {
  const params = new URLSearchParams({ type });
  return request(`/emails/by-priority-type?${params.toString()}`);
}

export function createAgent(payload) {
  return request("/agents", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateAgentRecord(id, payload) {
  return request(`/agents/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteAgentRecord(id) {
  return request(`/agents/${id}`, {
    method: "DELETE",
  });
}

export function getAllPrompts() {
  return request("/agent-prompts");
}

export function savePrompt(payload) {
  return request("/agent-prompts", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updatePrompt(id, payload) {
  return request(`/agent-prompts/${id}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deletePrompt(id) {
  return request(`/agent-prompts/${id}`, {
    method: "DELETE",
  });
}

export function getConfiguration(agentId) {
  return request(withAgentQuery("/config", agentId));
}

export function createConfigRecord(resource, payload) {
  return request(`/${resource}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateConfigRecord(resource, id, payload) {
  return request(`/${resource}/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteConfigRecord(resource, id) {
  return request(`/${resource}/${id}`, {
    method: "DELETE",
  });
}
