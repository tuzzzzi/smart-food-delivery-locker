const { getBaseUrl } = require("../../utils/request");
const BASE_URL = getBaseUrl();

function getToken() {
  return wx.getStorageSync("token") || wx.getStorageSync("openid") || "";
}

function authHeader() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

Page({
  data: {
    phone: "",
    code: "",
    hintText: "",
    reviewNote: "",
    sending: false,
    submitting: false,
    countdown: 0,
    timer: null,
    lastApplyStatus: "", // ✅用于检测状态变化

    // 状态：none / pending / approved / rejected
    applyStatus: "none",
    applyStatusText: "未提交",
    statusDesc: "请先完成手机号验证并提交申请",

    // pending/approved 状态时，锁定提交按钮（防重复）
    isLocked: false
  },

  goRoleSelect() {
    // 建议 reLaunch：避免返回到申请页
    wx.reLaunch({ url: "/pages/role-select/role-select" });
  },

  onUnload() {
    if (this.data.timer) clearInterval(this.data.timer);
  },

  onShow() {
    // 进入页面就尝试刷新一次状态（即使没接口也不影响）
    this.fetchProfilePhone();
    this.refreshStatus();
  },

  onPhone(e) {
    this.setData({ phone: (e.detail.value || "").trim() });
  },

  onCode(e) {
    const v = (e.detail.value || "").replace(/[^\d]/g, "");
    this.setData({ code: v });
  },

  setStatus(status) {
    let text = "未提交";
    let desc = "请先完成手机号验证并提交申请";
    if (status === "pending") text = "审核中";
    if (status === "pending") desc = "申请已提交，等待管理员审核";
    else if (status === "approved") {
      text = "已通过";
      desc = "你已获得配送员身份，可进入配送员工作台";
    } else if (status === "rejected") {
      text = "未通过";
      desc = "申请未通过，请联系管理员或重新提交";
    } else if (status === "none") {
      text = "未提交";
    }

    this.setData({
      applyStatus: status,
      applyStatusText: text,
      statusDesc: desc,
      isLocked: status === "pending" || status === "approved"
    });
  },

  fetchProfilePhone() {
    if (this.data.phone) return;
    const token = getToken();
    if (!token) return;

    wx.request({
      url: `${BASE_URL}/api/courier/status?openid=${encodeURIComponent(token)}`,
      method: "GET",
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") return;
        const phone = data.phoneNumber || data.phone_number || "";
        if (phone && !this.data.phone) {
          this.setData({ phone: String(phone).trim() });
        }
      }
    });
  },

  startCountdown(seconds = 60) {
    if (this.data.timer) clearInterval(this.data.timer);

    this.setData({ countdown: seconds });
    const t = setInterval(() => {
      const c = this.data.countdown - 1;
      if (c <= 0) {
        clearInterval(this.data.timer);
        this.setData({ countdown: 0, timer: null });
      } else {
        this.setData({ countdown: c });
      }
    }, 1000);

    this.setData({ timer: t });
  },

  refreshStatus() {
    const openid = wx.getStorageSync("openid") || "";
    if (!openid) {
      this.setStatus("none");
      this.setData({ reviewNote: "", lastApplyStatus: "none" });
      return;
    }
  
    wx.request({
      url: `${BASE_URL}/api/courier/status?openid=${encodeURIComponent(openid)}`,
      method: "GET",
      success: (res) => {
        const data = res.data || {};
  
        // ✅兼容：后端没返回 status 字段也能用（你之前遇到过）
        if (data.status && data.status !== "success") return;
  
        const s = data.apply_status || data.applyStatus || "none";
        const note = data.review_note || data.reviewNote || "";
  
        const prev = this.data.lastApplyStatus || this.data.applyStatus || "";
  
        // 先更新页面显示
        this.setStatus(s);
        this.setData({ reviewNote: note, lastApplyStatus: s });
  
        // ✅首次进入不弹（prev 为空）
        if (!prev) return;
  
        // ✅状态发生变化才弹
        if (s !== prev) {
          // pending -> approved
          if (prev === "pending" && s === "approved") {
            wx.showToast({ title: "审核已通过", icon: "success" });
            return;
          }
  
          // pending -> rejected
          if (prev === "pending" && s === "rejected") {
            wx.showToast({ title: "审核被拒绝", icon: "none" });
            return;
          }
  
          // none -> pending（刚提交）
          if (prev === "none" && s === "pending") {
            wx.showToast({ title: "已提交，等待审核", icon: "none" });
            return;
          }
  
          // 其它变化兜底提示
          wx.showToast({ title: `状态更新：${this.data.applyStatusText}`, icon: "none" });
        }
      },
      fail: () => {
        this.setData({ hintText: "网络错误：无法刷新状态" });
      }
    });
  },

  sendCode() {
    if (this.data.isLocked) {
      this.setData({ hintText: "当前状态无需重复发送验证码" });
      return;
    }

    const phone = (this.data.phone || "").trim();
    if (!phone || phone.length < 6) {
      this.setData({ hintText: "请输入正确手机号" });
      return;
    }

    const token = wx.getStorageSync("token");
    if (!token) {
      this.setData({ hintText: "请先登录" });
      return;
    }

    this.setData({ sending: true, hintText: "" });

    wx.request({
      url: `${BASE_URL}/api/courier/apply/send_code`,
      method: "POST",
      header: { "content-type": "application/json" },
      data: { token, phoneNumber: phone },
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

  submitVerify() {
    if (this.data.isLocked) {
      this.setData({ hintText: "当前状态无需重复提交" });
      return;
    }

    const phone = (this.data.phone || "").trim();
    const code = (this.data.code || "").trim();

    if (!phone) return this.setData({ hintText: "请输入手机号" });
    if (!code || code.length !== 6) return this.setData({ hintText: "请输入6位验证码" });

    const token = wx.getStorageSync("token");
    if (!token) return this.setData({ hintText: "请先登录" });

    this.setData({ submitting: true, hintText: "" });

    wx.request({
      url: `${BASE_URL}/api/courier/apply/verify`,
      method: "POST",
      header: { "content-type": "application/json" },
      data: { token, phoneNumber: phone, code },
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "验证失败" });
          return;
        }

        // ✅关键：不要在这里把 roles 写成 courier
        wx.setStorageSync("roles", ["user"]);

        // ✅本地直接进入待审核状态
        const s = data.apply_status || data.applyStatus || "pending";
        this.setStatus(s);

        wx.showToast({ title: "已提交，等待审核", icon: "success" });

        // 可选：返回上一页/角色选择页
        // wx.navigateBack();
        // wx.redirectTo({ url: "/pages/role-select/role-select" });
      },
      fail: () => this.setData({ hintText: "网络错误：验证失败" }),
      complete: () => this.setData({ submitting: false })
    });
  }
});
