const API_HOST = "127.0.0.1";
const API_PORT = "5000";
const API_PROTOCOL = "http:";
const DEFAULT_API_BASE_URL = `${API_PROTOCOL}//${API_HOST}:${API_PORT}`;
const LEGACY_API_HOSTS = ["127.0.0.1", "localhost"];

function trimBaseUrl(url) {
  return String(url || "").replace(/\/+$/, "");
}

function normalizeApiBaseUrl(url) {
  const trimmed = trimBaseUrl(url);
  if (!trimmed) {
    return DEFAULT_API_BASE_URL;
  }

  try {
    const parsed = new URL(trimmed);
    if (LEGACY_API_HOSTS.includes(parsed.hostname)) {
      parsed.protocol = API_PROTOCOL;
      parsed.hostname = API_HOST;
      parsed.port = API_PORT;
    } else if (!parsed.port) {
      parsed.port = API_PORT;
    }
    return trimBaseUrl(parsed.toString());
  } catch (error) {
    return DEFAULT_API_BASE_URL;
  }
}

function buildApiUrl(path) {
  const cleanPath = String(path || "");
  if (!cleanPath) {
    return DEFAULT_API_BASE_URL;
  }
  if (/^https?:\/\//i.test(cleanPath)) {
    return normalizeApiBaseUrl(cleanPath);
  }
  if (cleanPath.startsWith("/")) {
    return `${DEFAULT_API_BASE_URL}${cleanPath}`;
  }
  return `${DEFAULT_API_BASE_URL}/${cleanPath.replace(/^\/+/, "")}`;
}

module.exports = {
  API_HOST,
  API_PORT,
  API_PROTOCOL,
  DEFAULT_API_BASE_URL,
  LEGACY_API_HOSTS,
  trimBaseUrl,
  normalizeApiBaseUrl,
  buildApiUrl,
};
