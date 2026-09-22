const { getBaseUrl } = require("../../utils/request");
const BASE_URL = getBaseUrl();
const SERVICE_SCOPE = "陕西理工大学校园外卖柜配送";

function getToken() {
  return wx.getStorageSync("token") || wx.getStorageSync("openid") || "";
}

function authHeader() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function localProfileKey(openid) {
  return `courierProfileExtra:${openid || "default"}`;
}

function defaultProfile() {
  return {
    openid: "",
    avatarUrl: "",
    courierName: "",
    nickname: "",
    phoneNumber: "",
    email: "",
    serviceArea: SERVICE_SCOPE,
    applyStatus: "none",
    applyStatusText: "未提交",
    workStatus: "空闲"
  };
}

function applyStatusText(status) {
  if (status === "pending") return "审核中";
  if (status === "approved") return "已通过";
  if (status === "rejected") return "未通过";
  return "未提交";
}

Page({
  data: {
    profile: defaultProfile(),
    workStatusOptions: ["空闲", "配送中", "暂停接单"],
    workStatusIndex: 0,
    saving: false,
    hintText: ""
  },

  onLoad() {
    if (!getToken()) {
      wx.showToast({ title: "请先登录", icon: "none" });
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    this.fetchProfile();
    this.fetchCourierStatus();
  },

  loadLocal(openid) {
    const saved = wx.getStorageSync(localProfileKey(openid)) || {};
    const workStatus = this.data.profile.workStatus || "空闲";
    const index = Math.max(this.data.workStatusOptions.indexOf(workStatus), 0);
    this.setData({
      profile: {
        ...this.data.profile,
        openid,
        avatarUrl: saved.avatarUrl || this.data.profile.avatarUrl || "",
        serviceArea: SERVICE_SCOPE,
        workStatus
      },
      workStatusIndex: index
    });
  },

  fetchProfile() {
    wx.request({
      url: `${BASE_URL}/api/courier/profile`,
      method: "GET",
      header: authHeader(),
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "获取资料失败" });
          return;
        }
        const courier = data.courier || {};
        const openid = courier.openid || wx.getStorageSync("openid") || "";
        const applyStatus = courier.applyStatus || courier.apply_status || this.data.profile.applyStatus || "none";
        const workStatus = courier.workStatus || courier.work_status || this.data.profile.workStatus || "绌洪棽";
        const courierName = courier.courierName || courier.courier_name || courier.username || this.data.profile.courierName || "";
        const nickname = courier.nickname || courierName || this.data.profile.nickname || "";
        this.setData({
          profile: {
            ...this.data.profile,
            openid,
            courierName,
            nickname,
            phoneNumber: courier.phoneNumber || courier.phone_number || this.data.profile.phoneNumber || "",
            email: courier.email || this.data.profile.email || "",
            applyStatus,
            applyStatusText: applyStatusText(applyStatus),
            workStatus
          },
          hintText: ""
        });
        this.loadLocal(openid);
      },
      fail: () => this.setData({ hintText: "网络异常，暂时无法获取资料。" })
    });
  },

  fetchCourierStatus() {
    const openid = wx.getStorageSync("openid") || "";
    if (!openid) return;
    wx.request({
      url: `${BASE_URL}/api/courier/status?openid=${encodeURIComponent(openid)}`,
      method: "GET",
      success: (res) => {
        const data = res.data || {};
        if (data.status && data.status !== "success") return;
        const applyStatus = data.apply_status || data.applyStatus || "none";
        const phoneNumber = data.phone_number || data.phoneNumber || this.data.profile.phoneNumber || "";
        const workStatus = data.work_status || data.workStatus || this.data.profile.workStatus || "空闲";
        const courierName = data.courierName || data.courier_name || this.data.profile.courierName || "";
        const nickname = data.nickname || courierName || this.data.profile.nickname || "";
        const index = Math.max(this.data.workStatusOptions.indexOf(workStatus), 0);
        this.setData({
          profile: {
            ...this.data.profile,
            applyStatus,
            applyStatusText: applyStatusText(applyStatus),
            courierName,
            nickname,
            phoneNumber,
            workStatus
          },
          workStatusIndex: index
        });
      }
    });
  },

  onChooseAvatar(e) {
    const avatarUrl = e.detail && e.detail.avatarUrl ? e.detail.avatarUrl : "";
    if (avatarUrl) this.setData({ "profile.avatarUrl": avatarUrl });
  },

  onNameInput(e) {
    this.setData({ "profile.courierName": e.detail.value });
  },

  onNicknameInput(e) {
    this.setData({ "profile.nickname": e.detail.value });
  },

  onPhoneInput(e) {
    this.setData({ "profile.phoneNumber": e.detail.value });
  },

  onEmailInput(e) {
    this.setData({ "profile.email": e.detail.value });
  },

  onServiceAreaInput(e) {
    this.setData({ "profile.serviceArea": SERVICE_SCOPE });
  },

  onWorkStatusChange(e) {
    const index = Number(e.detail.value || 0);
    this.setData({
      workStatusIndex: index,
      "profile.workStatus": this.data.workStatusOptions[index] || "空闲"
    });
  },

  saveProfile() {
    const profile = this.data.profile;
    const phoneNumber = (profile.phoneNumber || "").trim();
    const email = (profile.email || "").trim();
    if (phoneNumber && !/^\d{11}$/.test(phoneNumber)) {
      wx.showToast({ title: "手机号必须为 11 位数字", icon: "none" });
      return;
    }
    if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      wx.showToast({ title: "邮箱格式不正确", icon: "none" });
      return;
    }

    const local = {
      avatarUrl: profile.avatarUrl || "",
      serviceArea: SERVICE_SCOPE
    };
    this.setData({ saving: true, hintText: "" });

    const url = `${BASE_URL}/api/courier/profile/update`;
    const payload = {
      courierName: (profile.courierName || "").trim(),
      nickname: (profile.nickname || "").trim(),
      phoneNumber,
      email,
      workStatus: profile.workStatus || "空闲"
    };
    console.log("[courier-profile:save] url", url);
    console.log("[courier-profile:save] payload", payload);

    wx.request({
      url,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data: payload,
      success: (res) => {
        console.log("[courier-profile:save] response", res.data);
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "保存失败" });
          return;
        }
        wx.setStorageSync(localProfileKey(profile.openid), local);
        wx.showToast({ title: "已保存", icon: "success" });
        this.fetchProfile();
      },
      fail: (err) => {
        console.log("[courier-profile:save] request failed", err);
        this.setData({ hintText: "网络异常，暂时无法保存资料。" });
      },
      complete: () => this.setData({ saving: false })
    });
  }
});
