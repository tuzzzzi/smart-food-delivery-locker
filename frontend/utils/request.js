const { DEFAULT_API_BASE_URL, normalizeApiBaseUrl } = require("./api-config");

const DEFAULT_BASE_URL = DEFAULT_API_BASE_URL;

function getBaseUrl() {
  let runtimeUrl = "";
  try {
    const app = typeof getApp === "function" ? getApp() : null;
    runtimeUrl = (app && app.globalData && (app.globalData.apiBaseUrl || app.globalData.baseUrl)) || "";
  } catch (error) {
    runtimeUrl = "";
  }

  let storedUrl = "";
  try {
    storedUrl = wx.getStorageSync("apiBaseUrl") || wx.getStorageSync("baseUrl") || "";
  } catch (error) {
    storedUrl = "";
  }

  return normalizeApiBaseUrl(runtimeUrl || storedUrl || DEFAULT_BASE_URL) || DEFAULT_BASE_URL;
}

function normalizeMediaUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";

  const baseUrl = getBaseUrl();
  let origin = baseUrl;
  try {
    const parsedBase = new URL(baseUrl);
    origin = `${parsedBase.protocol}//${parsedBase.host}`;
  } catch (error) {
    origin = baseUrl;
  }

  if (/^https?:\/\//i.test(raw)) {
    try {
      const parsed = new URL(raw);
      return `${origin}${parsed.pathname}${parsed.search}${parsed.hash}`;
    } catch (error) {
      return raw;
    }
  }

  if (raw.startsWith("/")) {
    return `${origin}${raw}`;
  }

  return `${origin}/${raw.replace(/^\/+/, "")}`;
}

function request({ url, method = "GET", data = {}, header = {}, success, fail, complete }) {
  const baseUrl = getBaseUrl();
  const token = wx.getStorageSync("token") || "";
  const finalHeader = {
    "content-type": "application/json",
    ...header
  };

  // 你后端当前是 body 里带 token，我这里也支持自动补
  if (token && typeof data === "object" && data !== null && !data.token) {
    data.token = token;
  }

  wx.request({
    url: baseUrl + url,
    method,
    data,
    header: finalHeader,
    success,
    fail,
    complete
  });
}

module.exports = {
  request,
  BASE_URL: getBaseUrl(),
  getBaseUrl,
  normalizeMediaUrl,
};
