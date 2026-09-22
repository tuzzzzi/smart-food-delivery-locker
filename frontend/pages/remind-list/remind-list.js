const { getBaseUrl } = require("../../utils/request");
const BASE_URL = getBaseUrl();

function getToken() {
  return wx.getStorageSync("token") || wx.getStorageSync("openid") || "";
}

function authHeader() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function formatTime(iso) {
  if (!iso) return "";
  return String(iso).replace("T", " ").slice(0, 16);
}

function reminderType(item) {
  const text = `${item.message || ""} ${item.type || ""} ${item.source || ""}`;
  if (/异常|失败|error|exception/i.test(text)) {
    return { typeText: "问题提醒", typeClass: "type-error" };
  }
  if (/超时|timeout|逾期/i.test(text)) {
    return { typeText: "超时提醒", typeClass: "type-timeout" };
  }
  return { typeText: "取件提醒", typeClass: "type-pickup" };
}

function friendlyMessage(message) {
  return String(message || "")
    .replace(/硬件闭环已完成/g, "柜门已关闭")
    .replace(/系统正在同步取件结果/g, "正在确认取件状态")
    .replace(/闭环完成/g, "已关门")
    .replace(/业务闭环|硬件闭环|取件闭环|闭环/g, "取件状态")
    .replace(/异常处理中/g, "问题处理中");
}

function normalizeItem(item) {
  const type = reminderType(item);
  return {
    ...item,
    ...type,
    message: friendlyMessage(item.message),
    statusText: item.readAt ? "已读" : "未读",
    sourceText: item.source === "admin" ? "管理员提醒" : "系统提醒",
    timeText: formatTime(item.sentAt || item.createdAt) || "时间同步中",
    merchantName: "",
    boxNo: ""
  };
}

Page({
  data: {
    items: [],
    unreadCount: 0,
    totalCount: 0,
    hintText: ""
  },

  onShow() {
    this.fetchList();
  },

  fetchList() {
    if (!getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }

    wx.request({
      url: `${BASE_URL}/api/remind/list?limit=50`,
      method: "GET",
      header: authHeader(),
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "获取提醒失败。" });
          return;
        }

        const items = (data.items || []).map(normalizeItem);
        this.setData({
          items,
          unreadCount: items.filter((item) => !item.readAt).length,
          totalCount: items.length,
          hintText: ""
        });
        this.enrichPackageInfo(items);
      },
      fail: () => {
        this.setData({ hintText: "网络异常，暂时无法获取提醒列表。" });
      }
    });
  },

  enrichPackageInfo(items) {
    const ids = Array.from(new Set((items || []).map((item) => item.packageId).filter(Boolean))).slice(0, 20);
    if (!ids.length) return;

    const detailMap = {};
    let left = ids.length;
    const finishOne = () => {
      left -= 1;
      if (left > 0) return;
      const merged = (this.data.items || []).map((item) => {
        const detail = detailMap[item.packageId] || {};
        return {
          ...item,
          merchantName: detail.merchantName || item.merchantName || "",
          boxNo: detail.boxNo || item.boxNo || ""
        };
      });
      this.setData({ items: merged });
    };

    ids.forEach((packageId) => {
      wx.request({
        url: `${BASE_URL}/api/user/package?packageId=${encodeURIComponent(packageId)}`,
        method: "GET",
        header: authHeader(),
        success: (res) => {
          const data = res.data || {};
          if (data.status === "success" && data.package) {
            detailMap[packageId] = data.package;
          }
        },
        complete: finishOne
      });
    });
  },

  markAllRead() {
    if (!getToken()) return;

    wx.request({
      url: `${BASE_URL}/api/remind/mark_read`,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data: { all: true },
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") return;
        wx.showToast({ title: "已全部标记", icon: "success" });
        this.fetchList();
      }
    });
  },

  markOneRead(e) {
    const id = e.currentTarget.dataset.id;
    this.markReadById(id, true);
  },

  markReadById(id, refresh) {
    if (!getToken() || !id) return;

    wx.request({
      url: `${BASE_URL}/api/remind/mark_read`,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data: { ids: [id] },
      success: () => {
        if (refresh) this.fetchList();
      }
    });
  },

  goGuide(e) {
    const packageId = e.currentTarget.dataset.pid || "";
    const id = e.currentTarget.dataset.id || "";
    if (!packageId) return;
    this.markReadById(id, false);
    wx.navigateTo({
      url: `/pages/guide/guide?role=user&packageId=${encodeURIComponent(packageId)}`
    });
  },

  goPackageDetail(e) {
    const packageId = e.currentTarget.dataset.pid || "";
    const id = e.currentTarget.dataset.id || "";
    if (!packageId) return;
    this.markReadById(id, false);
    wx.navigateTo({
      url: `/pages/take-package/take-package?role=user&packageId=${encodeURIComponent(packageId)}`
    });
  }
});
