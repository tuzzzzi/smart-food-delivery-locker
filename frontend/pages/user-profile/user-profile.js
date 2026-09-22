const { getBaseUrl } = require("../../utils/request");
const BASE_URL = getBaseUrl();

function getToken() {
  return wx.getStorageSync("token") || wx.getStorageSync("openid") || "";
}

function authHeader() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function clearAuthStorage() {
  wx.removeStorageSync("openid");
  wx.removeStorageSync("token");
  wx.removeStorageSync("roles");
  wx.removeStorageSync("currentRole");
  wx.removeStorageSync("username");
  wx.removeStorageSync("abandonedTaskId");
}

function localProfileKey(openid) {
  return `userProfileExtra:${openid || "default"}`;
}

function defaultExtraProfile() {
  return {
    avatarUrl: "",
    note: ""
  };
}

function readExtraProfile(openid) {
  const saved = wx.getStorageSync(localProfileKey(openid)) || {};
  return {
    avatarUrl: saved.avatarUrl || "",
    note: saved.note || ""
  };
}

function storedRoles() {
  const roles = wx.getStorageSync("roles");
  return Array.isArray(roles) ? roles : [];
}

function buildAccountView(profile, applyStatus) {
  const roles = storedRoles();
  const phoneNumber = (profile && profile.phoneNumber) || "";
  const isCourier = roles.includes("courier") || applyStatus === "approved";
  const isPending = applyStatus === "pending";

  let identityText = "普通用户";
  if (isCourier) {
    identityText = "配送员";
  } else if (isPending) {
    identityText = "配送员审核中";
  }

  let serviceTitle = "";
  let serviceDesc = "";
  let serviceAction = "";

  if (isCourier) {
    serviceTitle = "配送员工作台";
    serviceDesc = "进入投递任务和入柜管理";
    serviceAction = "进入 ›";
  } else if (isPending) {
    serviceTitle = "配送员身份审核中";
    serviceDesc = "管理员审核通过后即可使用配送功能";
    serviceAction = "查看状态 ›";
  }

  return {
    identityText,
    phoneStatusText: phoneNumber ? "已绑定" : "待完善",
    pickupMatchText: phoneNumber ? "已开启" : "待完善",
    serviceTitle,
    serviceDesc,
    serviceAction,
    serviceMode: isCourier ? "courier" : (isPending ? "pending" : "none")
  };
}

Page({
  data: {
    profile: {
      openid: "",
      username: "",
      phoneNumber: "",
      email: "",
      avatarUrl: "",
      receiverName: "",
      pickupArea: "",
      backupPhone: "",
      note: ""
    },
    form: {
      username: "",
      receiverName: "",
      phoneNumber: "",
      email: "",
      pickupArea: "",
      backupPhone: ""
    },
    origin: {
      username: "",
      phoneNumber: "",
      email: ""
    },
    courierApplyStatus: "none",
    accountView: buildAccountView({}, "none"),
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

  fetchProfile() {
    wx.request({
      url: `${BASE_URL}/api/profile/me`,
      method: "GET",
      header: authHeader(),
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "获取资料失败" });
          return;
        }

        const profile = data.user || data.profile || {};
        const username = profile.username || "";
        const phoneNumber = profile.phoneNumber || profile.phone_number || "";
        const email = profile.email || "";
        const openid = profile.openid || wx.getStorageSync("openid") || "";
        const extra = readExtraProfile(openid);
        const receiverName = profile.receiverName || profile.receiver_name || extra.receiverName || "";
        const pickupArea = profile.pickupArea || profile.pickup_area || extra.pickupArea || "";
        const backupPhone = profile.backupPhone || profile.backup_phone || extra.backupPhone || "";
        const form = { username, receiverName, phoneNumber, email, pickupArea, backupPhone };

        this.setData({
          profile: { openid, ...extra },
          form,
          origin: { username, phoneNumber, email },
          accountView: buildAccountView(form, this.data.courierApplyStatus),
          hintText: ""
        });
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
        this.setData({
          courierApplyStatus: applyStatus,
          accountView: buildAccountView(this.data.form, applyStatus)
        });
      }
    });
  },

  onFormInput(e) {
    const field = e.currentTarget.dataset.field;
    if (!field) return;
    this.setData({ [`form.${field}`]: (e.detail && e.detail.value) || "" });
  },

  onNoteInput(e) {
    this.setData({ "profile.note": e.detail.value });
  },

  onChooseAvatar(e) {
    const avatarUrl = e.detail && e.detail.avatarUrl ? e.detail.avatarUrl : "";
    if (avatarUrl) this.setData({ "profile.avatarUrl": avatarUrl });
  },

  saveProfile() {
    console.log("[user-profile:save] current form", this.data.form);
    const form = this.data.form || {};
    let username = (form.username || "").trim();
    let phoneNumber = (form.phoneNumber || "").trim();
    const email = (form.email || "").trim();
    const backupPhone = (form.backupPhone || "").trim();

    const oldUsername = (this.data.origin.username || "").trim();
    const oldPhone = (this.data.origin.phoneNumber || "").trim();

    if (!username) username = oldUsername;
    if (!phoneNumber) phoneNumber = oldPhone;

    const extra = {
      avatarUrl: this.data.profile.avatarUrl || "",
      note: (this.data.profile.note || "").trim()
    };
    const receiverName = (form.receiverName || "").trim();
    const pickupArea = (form.pickupArea || "").trim();

    const hasAnyProfileValue = !!(
      username ||
      phoneNumber ||
      email ||
      receiverName ||
      pickupArea ||
      backupPhone ||
      extra.avatarUrl ||
      extra.note
    );

    if (!hasAnyProfileValue) {
      wx.showToast({ title: "请填写资料后再保存", icon: "none" });
      return;
    }

    if (phoneNumber && !/^\d{11}$/.test(phoneNumber)) {
      wx.showToast({ title: "手机号必须为 11 位数字", icon: "none" });
      return;
    }
    if (backupPhone && !/^\d{11}$/.test(backupPhone)) {
      wx.showToast({ title: "备用联系电话需为 11 位数字", icon: "none" });
      return;
    }
    if (email && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      wx.showToast({ title: "邮箱格式不正确", icon: "none" });
      return;
    }

    this.setData({ saving: true, hintText: "" });

    const url = `${BASE_URL}/api/user/profile/update`;
    const payload = { username, phoneNumber, email, receiverName, pickupArea, backupPhone };
    console.log("[user-profile:save] url", url);
    console.log("[user-profile:save] payload", payload);

    wx.request({
      url,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data: payload,
      success: (res) => {
        console.log("[user-profile:save] response", res.data);
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "保存失败" });
          return;
        }

        wx.setStorageSync(localProfileKey(this.data.profile.openid), extra);
        wx.setStorageSync("username", username);
        wx.showToast({ title: "已保存", icon: "success" });
        this.fetchProfile();

        const pages = getCurrentPages();
        const prev = pages[pages.length - 2];
        if (prev && prev.setData) prev.setData({ needRefresh: true });
      },
      fail: (err) => {
        console.log("[user-profile:save] request failed", err);
        this.setData({ hintText: "网络异常，暂时无法保存资料。" });
      },
      complete: () => this.setData({ saving: false })
    });
  },

  goApplyCourier() {
    wx.navigateTo({
      url: "/pages/courier-apply/courier-apply",
      fail: (err) => {
        console.log("goApplyCourier fail:", err);
        wx.showToast({ title: "暂时无法打开申请页面", icon: "none" });
      }
    });
  },

  goMoreService() {
    if (this.data.accountView.serviceMode === "courier") {
      wx.navigateTo({
        url: "/pages/courier-home/courier-home",
        fail: () => wx.reLaunch({ url: "/pages/courier-home/courier-home" })
      });
      return;
    }
    if (this.data.accountView.serviceMode === "pending") {
      this.goApplyCourier();
    }
  },

  switchIdentity() {
    clearAuthStorage();
    wx.reLaunch({ url: "/pages/login/login" });
  }
});
