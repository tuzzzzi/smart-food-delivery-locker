const { getBaseUrl } = require("../../utils/request");
const { scanAndOpenVerify } = require("../../utils/qr-verify");
const BASE_URL = getBaseUrl();

function getToken() {
  return wx.getStorageSync("token") || wx.getStorageSync("openid") || "";
}

function authHeader() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(Number(ts) * 1000);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

function friendlyText(text) {
  return String(text || "")
    .replace(/硬件闭环已完成/g, "柜门已关闭")
    .replace(/系统正在同步取件结果/g, "正在确认取件状态")
    .replace(/闭环完成/g, "已关门")
    .replace(/业务闭环|硬件闭环|取件闭环|闭环/g, "取件状态")
    .replace(/异常处理中/g, "问题处理中");
}

function getPickupCode(pkg) {
  return String(
    (pkg && (pkg.pickupCode || pkg.pickup_code || pkg.code || pkg.pickupCodeText)) ||
    ""
  ).trim();
}

Page({
  data: {
    userOpenid: "",
    userName: "",
    userShort: "",

    unreadCount: 0,
    unreadTimer: null,
    remindTimer: null,
    unreadInFlight: false,
    pollInFlight: false,

    pendingList: [],
    historyList: [],
    focusPending: null,
    pendingCount: 0,
    otherPendingCount: 0,
    historyCount: 0,

    needRefresh: false,
    hintText: ""
  },

  _openPackageDetail(packageId, role = "user") {
    if (!packageId) return;
    wx.navigateTo({
      url: `/pages/take-package/take-package?packageId=${encodeURIComponent(packageId)}&role=${encodeURIComponent(role)}`
    });
  },

  onLoad() {
    const openid = wx.getStorageSync("openid") || "";
    if (!openid || !getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }

    const short = openid.length > 10 ? `${openid.slice(0, 10)}...` : openid;
    const cachedName = wx.getStorageSync("username") || "";

    this.setData({
      userOpenid: openid,
      userShort: short,
      userName: cachedName || "未设置昵称"
    });

    this.fetchProfileAndCache();
  },

  onShow() {
    if (this.data.needRefresh) {
      this.setData({ needRefresh: false });
      this.fetchProfileAndCache();
    }

    this.refresh();
    this.refreshUnreadAndPollOnce();
    this.startUnreadTimer();
    this.startRemindPolling();
  },

  onPullDownRefresh() {
    this.fetchProfileAndCache();
    this.refreshUnreadAndPollOnce();
    this.refresh(() => wx.stopPullDownRefresh());
  },

  onHide() {
    this.stopUnreadTimer();
    this.stopRemindPolling();
  },

  onUnload() {
    this.stopUnreadTimer();
    this.stopRemindPolling();
  },

  goProfile() {
    wx.navigateTo({ url: "/pages/user-profile/user-profile" });
  },

  goRemindList() {
    wx.navigateTo({ url: "/pages/remind-list/remind-list" });
  },

  goSmartService() {
    wx.navigateTo({ url: "/pages/smart-service/smart-service?role=user" });
  },

  scanVerifyQr() {
    scanAndOpenVerify("user");
  },

  refreshUnreadAndPollOnce() {
    if (!getToken()) return;
    this.fetchUnreadCount();
    this.pollRemindsOnce();
  },

  pollRemindsOnce() {
    if (this.data.pollInFlight || !getToken()) return;
    this.setData({ pollInFlight: true });

    wx.request({
      url: `${BASE_URL}/api/remind/poll`,
      method: "GET",
      header: authHeader(),
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") return;

        const reminds = data.reminds || [];
        if (!reminds.length) return;

        const first = reminds[0];
        wx.showToast({
          title: friendlyText(first.message) || "你有新的提醒",
          icon: "none",
          duration: 2000
        });

        this.fetchUnreadCount();
      },
      complete: () => {
        this.setData({ pollInFlight: false });
      }
    });
  },

  fetchUnreadCount() {
    if (!getToken() || this.data.unreadInFlight) return;

    this.setData({ unreadInFlight: true });

    wx.request({
      url: `${BASE_URL}/api/remind/unread_count`,
      method: "GET",
      header: authHeader(),
      success: (res) => {
        const data = res.data || {};
        if (data.status === "success") {
          this.setData({ unreadCount: data.count || 0 });
        }
      },
      complete: () => {
        this.setData({ unreadInFlight: false });
      }
    });
  },

  startUnreadTimer() {
    if (this.data.unreadTimer) return;

    const timer = setInterval(() => {
      if (!getToken()) return;
      this.fetchUnreadCount();
    }, 8000);

    this.setData({ unreadTimer: timer });
  },

  stopUnreadTimer() {
    if (this.data.unreadTimer) {
      clearInterval(this.data.unreadTimer);
      this.setData({ unreadTimer: null });
    }
  },

  startRemindPolling() {
    if (this.data.remindTimer || !getToken()) return;

    const tick = () => {
      if (this.data.pollInFlight) return;
      this.pollRemindsOnce();
    };

    const timer = setInterval(tick, 12000);
    this.setData({ remindTimer: timer });
  },

  stopRemindPolling() {
    if (this.data.remindTimer) {
      clearInterval(this.data.remindTimer);
      this.setData({ remindTimer: null });
    }
  },

  refresh(done) {
    this.setData({ hintText: "" });

    wx.request({
      url: `${BASE_URL}/api/user/home`,
      method: "GET",
      header: authHeader(),
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "加载失败" });
          done && done();
          return;
        }

        const pending = (data.pending || []).map((p) => ({
          ...p,
          arrivedAtText: fmtTime(p.arrivedAt),
          pickupCodeText: getPickupCode(p),
          statusText: p.status === "pending" ? "待取件" : p.status,
          actionText: "立即去取"
        }));

        const history = (data.history || []).map((p) => ({
          ...p,
          pickedAtText: p.pickedAt ? `取件：${fmtTime(p.pickedAt)}` : "已取件",
          statusText: "已取件"
        }));

        this.setData({
          pendingList: pending,
          historyList: history,
          focusPending: pending[0] || null,
          pendingCount: pending.length,
          otherPendingCount: Math.max(pending.length - 1, 0),
          historyCount: history.length
        });
        done && done();
      },
      fail: () => {
        this.setData({ hintText: "网络错误：无法加载首页数据。" });
        done && done();
      }
    });
  },

  fetchProfileAndCache() {
    if (!getToken()) return;

    wx.request({
      url: `${BASE_URL}/api/profile/me`,
      method: "GET",
      header: authHeader(),
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success" || !data.user) return;

        const name = data.user.username || "";
        if (name) {
          wx.setStorageSync("username", name);
          this.setData({ userName: name });
        }
      }
    });
  },

  goGuide(e) {
    const pid = e.currentTarget.dataset.pid;
    if (!pid) return;
    console.log("[user-home:goGuide]", { packageId: pid, url: `/pages/guide/guide?role=user&packageId=${encodeURIComponent(pid)}` });
    wx.navigateTo({
      url: `/pages/guide/guide?role=user&packageId=${encodeURIComponent(pid)}`
    });
  },

  goFirstPending() {
    const pkg = (this.data.pendingList || [])[0];
    if (!pkg) {
      this.openPickupByCode();
      return;
    }
    console.log("[user-home:goFirstPending]", {
      packageId: pkg.packageId,
      boxNo: pkg.boxNo,
      url: `/pages/guide/guide?role=user&packageId=${encodeURIComponent(pkg.packageId)}`
    });
    wx.navigateTo({
      url: `/pages/guide/guide?role=user&packageId=${encodeURIComponent(pkg.packageId)}`
    });
  },

  openPickupByCode() {
    wx.navigateTo({ url: "/pages/take-package/take-package" });
  },

  goPackageDetail(e) {
    const pid = (e.currentTarget.dataset.pid || "").trim();
    this._openPackageDetail(pid, "user");
  },

  goFirstPendingDetail() {
    const pkg = (this.data.pendingList || [])[0];
    if (!pkg || !pkg.packageId) {
      this.openPickupByCode();
      return;
    }
    this._openPackageDetail(pkg.packageId, "user");
  },

  goPickupHelp() {
    const firstPending = (this.data.pendingList || [])[0] || {};
    const packageId = firstPending.packageId || "";
    const url =
      `/pages/delivery-error/delivery-error?role=user&scene=manual&packageId=${encodeURIComponent(packageId)}` +
      "&message=" + encodeURIComponent("需要查看取件失败后的处理方式。");
    wx.navigateTo({ url });
  }
});
