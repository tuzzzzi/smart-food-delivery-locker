const { getBaseUrl } = require("../../utils/request");
const BASE_URL = getBaseUrl();

function getToken() {
  return wx.getStorageSync("token") || wx.getStorageSync("openid") || "";
}

function authHeader() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function normalizeRole(role) {
  return role === "courier" ? "courier" : "user";
}

Page({
  data: {
    selectedRole: "user",
    nickname: "",
    receiverName: "",
    courierName: "",
    phoneNumber: "",
    email: "",
    pickupArea: "",
    backupPhone: "",
    code: "",

    loading: false,
    gettingPhone: false,
    sending: false,
    countdown: 0,
    timer: null,
    hintText: ""
  },

  onLoad() {
    this.fetchProfile();
  },

  onUnload() {
    if (this.data.timer) clearInterval(this.data.timer);
  },

  fetchProfile() {
    if (!getToken()) return;

    wx.request({
      url: `${BASE_URL}/api/profile/me`,
      method: "GET",
      header: authHeader(),
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success" || !data.user) return;

        const user = data.user || {};
        const username = user.username || "";
        const role = normalizeRole(user.role || "user");
        this.setData({
          selectedRole: role,
          nickname: username,
          courierName: username,
          receiverName: user.receiverName || user.receiver_name || "",
          phoneNumber: user.phoneNumber || user.phone_number || "",
          email: user.email || "",
          pickupArea: user.pickupArea || user.pickup_area || "",
          backupPhone: user.backupPhone || user.backup_phone || ""
        });
      }
    });
  },

  selectRole(e) {
    const role = normalizeRole(e.currentTarget.dataset.role);
    this.setData({ selectedRole: role, hintText: "" });
  },

  onNickInput(e) {
    this.setData({ nickname: e.detail.value });
  },

  onCourierNameInput(e) {
    this.setData({ courierName: e.detail.value });
  },

  onReceiverNameInput(e) {
    this.setData({ receiverName: e.detail.value });
  },

  onPhoneInput(e) {
    this.setData({ phoneNumber: e.detail.value });
  },

  onCodeInput(e) {
    const value = (e.detail.value || "").replace(/[^\d]/g, "");
    this.setData({ code: value });
  },

  onEmailInput(e) {
    this.setData({ email: e.detail.value });
  },

  onPickupAreaInput(e) {
    this.setData({ pickupArea: e.detail.value });
  },

  onBackupPhoneInput(e) {
    this.setData({ backupPhone: e.detail.value });
  },

  onGetPhoneNumber(e) {
    if (!getToken()) {
      this.setData({ hintText: "请先登录" });
      return;
    }

    const phoneCode = (e.detail && e.detail.code) || "";
    if (!phoneCode) {
      this.setData({ hintText: "请在真机授权手机号，或手动输入手机号。" });
      return;
    }
    this.setData({ gettingPhone: true, hintText: "" });
    wx.request({
      url: `${BASE_URL}/api/profile/phone`,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data: { phoneCode },
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "获取手机号失败" });
          return;
        }
        this.setData({ phoneNumber: data.phoneNumber || "", hintText: "" });
        wx.showToast({ title: "手机号已获取", icon: "success" });
      },
      fail: () => this.setData({ hintText: "网络异常，获取手机号失败" }),
      complete: () => this.setData({ gettingPhone: false })
    });
  },

  startCountdown(seconds = 60) {
    if (this.data.timer) clearInterval(this.data.timer);

    this.setData({ countdown: seconds });
    const timer = setInterval(() => {
      const countdown = this.data.countdown - 1;
      if (countdown <= 0) {
        clearInterval(this.data.timer);
        this.setData({ countdown: 0, timer: null });
        return;
      }
      this.setData({ countdown });
    }, 1000);

    this.setData({ timer });
  },

  sendCode() {
    if (!getToken()) {
      this.setData({ hintText: "请先登录" });
      return;
    }

    const phoneNumber = (this.data.phoneNumber || "").trim();
    if (!/^\d{11}$/.test(phoneNumber)) {
      this.setData({ hintText: "请输入 11 位手机号" });
      return;
    }

    const selectedRole = normalizeRole(this.data.selectedRole);
    const token = getToken();
    const isCourier = selectedRole === "courier";
    const url = isCourier
      ? `${BASE_URL}/api/courier/apply/send_code`
      : `${BASE_URL}/api/profile/send_code`;
    const data = isCourier
      ? { token, phoneNumber }
      : { phoneNumber };

    this.setData({ sending: true, hintText: "" });
    wx.request({
      url,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data,
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "发送失败" });
          return;
        }
        wx.showToast({ title: "验证码已发送", icon: "success" });
        this.startCountdown(60);
      },
      fail: () => this.setData({ hintText: "网络错误：发送失败" }),
      complete: () => this.setData({ sending: false })
    });
  },

  routeAfterSave(data) {
    const applyStatus = data.apply_status || data.applyStatus || "";
    const roles = Array.isArray(data.roles) ? data.roles : [];

    if (this.data.selectedRole === "courier") {
      wx.setStorageSync("roles", roles);
      if (applyStatus === "approved" || roles.includes("courier")) {
        wx.setStorageSync("currentRole", "courier");
        wx.reLaunch({ url: "/pages/courier-home/courier-home" });
        return;
      }
      wx.removeStorageSync("currentRole");
      wx.reLaunch({ url: "/pages/courier-apply/courier-apply" });
      return;
    }

    wx.setStorageSync("roles", ["user"]);
    wx.setStorageSync("currentRole", "user");
    wx.reLaunch({ url: "/pages/user-home/user-home" });
  },

  saveProfile(payload) {
    wx.request({
      url: `${BASE_URL}/api/profile/complete`,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data: payload,
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "保存失败" });
          return;
        }

        wx.setStorageSync("username", payload.username);
        wx.showToast({ title: "资料已保存", icon: "success" });

        setTimeout(() => this.routeAfterSave(data), 300);
      },
      fail: () => this.setData({ hintText: "网络错误：保存失败" }),
      complete: () => this.setData({ loading: false })
    });
  },

  submitCourierRegistration(payload) {
    const token = getToken();
    wx.request({
      url: `${BASE_URL}/api/courier/apply/verify`,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data: {
        token,
        phoneNumber: payload.phoneNumber,
        code: (this.data.code || "").trim(),
        courierName: payload.courierName || payload.username,
        nickname: payload.nickname || payload.username
      },
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ loading: false, hintText: data.message || "验证码错误" });
          return;
        }

        wx.setStorageSync("username", payload.username);
        wx.setStorageSync("roles", Array.isArray(data.roles) ? data.roles : []);
        wx.showToast({ title: "已提交审核", icon: "success" });
        setTimeout(() => this.routeAfterSave(data), 300);
      },
      fail: () => {
        this.setData({
          loading: false,
          hintText: "网络错误：验证失败"
        });
      },
      complete: () => this.setData({ loading: false })
    });
  },

  buildPayload() {
    const selectedRole = normalizeRole(this.data.selectedRole);
    const username = selectedRole === "courier"
      ? (this.data.courierName || "").trim()
      : (this.data.nickname || "").trim();

    return {
      role: selectedRole,
      selectedRole,
      username,
      courierName: selectedRole === "courier" ? username : "",
      nickname: selectedRole === "courier" ? username : (this.data.nickname || "").trim(),
      receiverName: selectedRole === "user" ? (this.data.receiverName || "").trim() : "",
      phoneNumber: (this.data.phoneNumber || "").trim(),
      email: (this.data.email || "").trim(),
      pickupArea: selectedRole === "user" ? (this.data.pickupArea || "").trim() : "",
      backupPhone: selectedRole === "user" ? (this.data.backupPhone || "").trim() : ""
    };
  },

  validatePayload(payload) {
    if (!payload.username) {
      this.setData({ hintText: payload.role === "courier" ? "请输入配送员姓名" : "请输入昵称" });
      return false;
    }
    if (!payload.phoneNumber) {
      this.setData({ hintText: "请输入手机号" });
      return false;
    }
    if (payload.role === "user" && !payload.receiverName) {
      this.setData({ hintText: "请输入收件人姓名" });
      return false;
    }
    if (!/^\d{11}$/.test(payload.phoneNumber)) {
      this.setData({ hintText: "请输入 11 位手机号" });
      return false;
    }
    if (!/^\d{6}$/.test((this.data.code || "").trim())) {
      this.setData({ hintText: "请输入 6 位验证码" });
      return false;
    }
    if (payload.backupPhone && !/^\d{11}$/.test(payload.backupPhone)) {
      this.setData({ hintText: "备用联系电话需为 11 位手机号" });
      return false;
    }
    if (payload.email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(payload.email)) {
      this.setData({ hintText: "邮箱格式不正确" });
      return false;
    }
    return true;
  },

  submit() {
    if (!getToken()) {
      this.setData({ hintText: "请先登录" });
      return;
    }

    const payload = this.buildPayload();
    if (!this.validatePayload(payload)) return;
    if (this.data.loading) return;

    this.setData({ loading: true, hintText: "" });
    if (payload.role === "courier") {
      this.submitCourierRegistration(payload);
      return;
    }

    wx.request({
      url: `${BASE_URL}/api/profile/verify_code`,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data: { phoneNumber: payload.phoneNumber, code: (this.data.code || "").trim() },
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ loading: false, hintText: data.message || "验证码错误" });
          return;
        }
        this.saveProfile(payload);
      },
      fail: () => {
        this.setData({
          loading: false,
          hintText: "网络错误：验证失败"
        });
      }
    });
  }
});
