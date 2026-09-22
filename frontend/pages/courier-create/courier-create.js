const { getBaseUrl } = require("../../utils/request");
const BASE_URL = getBaseUrl();

function getToken() {
  return wx.getStorageSync("token") || wx.getStorageSync("openid") || "";
}

function requireLoginOrRedirect() {
  const openid = wx.getStorageSync("openid") || "";
  const token = getToken();
  if (!openid || !token) {
    wx.showToast({ title: "请先登录", icon: "none" });
    wx.reLaunch({ url: "/pages/login/login" });
    return { ok: false, openid: "", token: "" };
  }
  return { ok: true, openid, token };
}

function resolvePhoneMatchMessage(reasonCode, fallbackMessage) {
  if (reasonCode === "invalid_phone_format") {
    return "手机号格式不正确，请输入 11 位手机号";
  }
  if (reasonCode === "user_not_found") {
    return "该手机号还未绑定用户，无法创建投递任务";
  }
  if (reasonCode === "duplicate_phone_match") {
    return "该手机号匹配到多个用户，请联系管理员确认";
  }
  return fallbackMessage || "创建失败";
}

Page({
  data: {
    receiverPhone: "",
    merchantName: "",
    creating: false,
    needRefresh: false
  },

  onLoad() {},

  onShow() {
    const auth = requireLoginOrRedirect();
    if (!auth.ok) return;

    const abandonedTaskId = wx.getStorageSync("abandonedTaskId");
    if (abandonedTaskId) {
      wx.removeStorageSync("abandonedTaskId");
      wx.showToast({ title: "已返回未完成任务", icon: "none" });
      setTimeout(() => {
        wx.redirectTo({
          url: `/pages/guide/guide?role=courier&taskId=${encodeURIComponent(abandonedTaskId)}`
        });
      }, 250);
      return;
    }

    this.refreshPage();
  },

  refreshPage() {
    this.setData({
      receiverPhone: "",
      merchantName: "",
      creating: false,
      needRefresh: false
    });
  },

  onReceiverInput(e) {
    this.setData({ receiverPhone: e.detail.value });
  },

  onMerchantInput(e) {
    this.setData({ merchantName: e.detail.value });
  },

  createTaskAndGo() {
    const auth = requireLoginOrRedirect();
    if (!auth.ok) return;

    const receiverPhone = (this.data.receiverPhone || "").trim();
    const merchantName = (this.data.merchantName || "").trim();
    const courierOpenid = auth.openid;

    if (!receiverPhone) {
      wx.showToast({ title: "请输入收件人手机号", icon: "none" });
      return;
    }

    if (!/^\d{11}$/.test(receiverPhone)) {
      wx.showToast({ title: "请输入 11 位手机号", icon: "none" });
      return;
    }

    this.setData({ creating: true });

    wx.request({
      url: `${BASE_URL}/api/courier/create_task`,
      method: "POST",
      header: {
        "content-type": "application/json",
        Authorization: `Bearer ${auth.token}`
      },
      data: { courierOpenid, receiverPhone, merchantName },
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          wx.showToast({
              title: resolvePhoneMatchMessage(data.reasonCode, data.message || "创建失败"),
            icon: "none"
          });
          return;
        }

        const taskId = data.task && data.task.taskId;
        if (!taskId) {
          wx.showToast({ title: "任务创建异常，请刷新后重试", icon: "none" });
          return;
        }

        wx.showToast({ title: "已分配可用柜", icon: "success" });
        setTimeout(() => {
          wx.navigateTo({
            url: `/pages/guide/guide?role=courier&taskId=${encodeURIComponent(taskId)}`
          });
        }, 250);
      },
      fail: () => {
        wx.showToast({ title: "网络异常", icon: "none" });
      },
      complete: () => {
        this.setData({ creating: false });
      }
    });
  }
});
