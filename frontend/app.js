const { DEFAULT_API_BASE_URL, normalizeApiBaseUrl } = require("./utils/api-config");

App({
  globalData: {
    apiBaseUrl: DEFAULT_API_BASE_URL
  },

  onLaunch() {
    let resolvedUrl = DEFAULT_API_BASE_URL;

    try {
      const storedUrl = wx.getStorageSync("apiBaseUrl") || wx.getStorageSync("baseUrl") || "";
      resolvedUrl = normalizeApiBaseUrl(storedUrl || DEFAULT_API_BASE_URL);
      wx.setStorageSync("apiBaseUrl", resolvedUrl);
      wx.setStorageSync("baseUrl", resolvedUrl);
    } catch (error) {
      resolvedUrl = DEFAULT_API_BASE_URL;
    }

    this.globalData.apiBaseUrl = resolvedUrl;
    this.globalData.baseUrl = resolvedUrl;
  }
});
