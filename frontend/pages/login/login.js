const { getBaseUrl } = require("../../utils/request");
const BASE_URL = getBaseUrl();

const DEMO_GROUPS = [
  { key: "pickup_user", title: "用户测试" },
  { key: "courier", title: "配送员测试" },
  { key: "review", title: "审核状态测试" },
  { key: "register", title: "新用户注册测试" }
];

const DEMO_LABELS = {
  user_a: "用户A",
  user_b: "用户B",
  user_c: "用户C",
  new_user: "新用户注册测试",
  courier_a: "配送员A",
  courier_b: "配送员B",
  courier_c: "配送员C",
  courier_d: "配送员D",
  courier_e: "配送员E",
  courier_pending: "待审核配送员",
  courier_rejected: "已驳回配送员"
};

function demoLabel(item) {
  return DEMO_LABELS[item && item.key] || item.label || item.username || "测试账号";
}

function groupIdentities(identities) {
  return DEMO_GROUPS
    .map((group) => ({
      ...group,
      items: identities
        .filter((item) => (item.demoGroup || "") === group.key)
        .map((item) => ({
          ...item,
          displayLabel: demoLabel(item)
        }))
    }))
    .filter((group) => group.items.length > 0);
}

Page({
  data: {
    logging: false,
    loadingMockList: true,
    mockMode: false,
    demoExpanded: false,
    mockGroups: [],
    activeMockKey: "",
    hintText: ""
  },

  onLoad() {
    this.loadMockIdentities();
  },

  toggleDemo() {
    if (this.data.logging) return;
    this.setData({ demoExpanded: !this.data.demoExpanded });
  },

  loadMockIdentities() {
    wx.request({
      url: `${BASE_URL}/api/mock/identities?audience=miniapp`,
      method: "GET",
      success: (res) => {
        const data = res.data || {};
        const identities = Array.isArray(data.identities) ? data.identities : [];
        const mockGroups = groupIdentities(identities);
        if (data.status === "success" && mockGroups.length) {
          this.setData({
            mockMode: true,
            mockGroups,
            hintText: ""
          });
          return;
        }

        this.setData({ mockMode: false, mockGroups: [] });
      },
      fail: () => {
        this.setData({ mockMode: false, mockGroups: [] });
      },
      complete: () => {
        this.setData({ loadingMockList: false });
      }
    });
  },

  applyLoginResult(data) {
    const openid = data.openid;
    const token = data.token || openid;
    const roles = Array.isArray(data.roles) ? data.roles : [];
    const needProfile = !!data.needProfile;
    const identity = data.identity || null;
    const courierApplyStatus = data.courierApplyStatus || (identity ? (identity.courierApplyStatus || "") : "");

    wx.setStorageSync("openid", openid);
    wx.setStorageSync("token", token);
    wx.setStorageSync("roles", roles);
    wx.removeStorageSync("currentRole");
    wx.removeStorageSync("abandonedTaskId");
    if (identity && identity.username) {
      wx.setStorageSync("username", identity.username);
    }
    wx.removeStorageSync("mockIdentityKey");

    if (needProfile) {
      wx.reLaunch({ url: "/pages/register/register" });
      return;
    }

    if (courierApplyStatus === "pending" || courierApplyStatus === "rejected") {
      wx.reLaunch({ url: "/pages/courier-apply/courier-apply" });
      return;
    }

    if (roles.includes("courier")) {
      wx.setStorageSync("currentRole", "courier");
      wx.reLaunch({ url: "/pages/courier-home/courier-home" });
      return;
    }

    if (roles.includes("user")) {
      wx.setStorageSync("currentRole", "user");
      wx.reLaunch({ url: "/pages/user-home/user-home" });
      return;
    }

    wx.reLaunch({ url: "/pages/user-home/user-home" });
  },

  doMockLogin(e) {
    const key = typeof e === "string" ? e : (e.currentTarget.dataset.key || "");
    if (!key || this.data.logging) return;

    this.setData({ logging: true, activeMockKey: key, hintText: "" });

    wx.request({
      url: `${BASE_URL}/api/login`,
      method: "POST",
      header: { "content-type": "application/json" },
      data: { mockIdentityKey: key },
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "登录失败，请重试。" });
          return;
        }
        this.applyLoginResult(data);
      },
      fail: () => this.setData({ hintText: "网络异常，暂时无法登录。" }),
      complete: () => this.setData({ logging: false, activeMockKey: "" })
    });
  },

  doLogin() {
    this.setData({ logging: true, hintText: "" });

    wx.login({
      success: (wxRes) => {
        if (!wxRes.code) {
          this.setData({ hintText: "微信登录未返回有效凭证，请重试。", logging: false });
          return;
        }

        wx.request({
          url: `${BASE_URL}/api/login`,
          method: "POST",
          header: { "content-type": "application/json" },
          data: { code: wxRes.code },
          success: (res) => {
            const data = res.data || {};
            if (data.status !== "success") {
              this.setData({ hintText: data.message || "登录失败，请重试。" });
              return;
            }
            this.applyLoginResult(data);
          },
          fail: () => this.setData({ hintText: "网络异常，暂时无法登录。" }),
          complete: () => this.setData({ logging: false })
        });
      },
      fail: () => {
        this.setData({ logging: false, hintText: "微信登录失败，请重试。" });
      }
    });
  }
});
