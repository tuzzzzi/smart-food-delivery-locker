const { getBaseUrl } = require("../../utils/request");
const { parseQrVerifyToken } = require("../../utils/qr-verify");

const BASE_URL = getBaseUrl();

function pickToken(options) {
  const values = [
    options.token,
    options.qr_token,
    options.qrToken,
    options.scene ? decodeURIComponent(options.scene) : "",
    options.path ? decodeURIComponent(options.path) : "",
    options.q ? decodeURIComponent(options.q) : ""
  ];

  for (const value of values) {
    const token = parseQrVerifyToken(value);
    if (token) return token;
  }
  return "";
}

Page({
  data: {
    token: "",
    from: "",
    loading: false,
    errorText: "",
    verifyInfo: null
  },

  onLoad(options) {
    const token = pickToken(options || {});
    const from = String((options && options.from) || "").trim();
    this.setData({ token, from });
    if (!token) {
      this.setData({ errorText: "二维码无效或订单不存在" });
      return;
    }
    this.loadVerifyInfo(token);
  },

  loadVerifyInfo(token) {
    this.setData({ loading: true, errorText: "", verifyInfo: null });

    wx.request({
      url: `${BASE_URL}/api/order/verify_qr/${encodeURIComponent(token)}`,
      method: "GET",
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ errorText: data.message || "二维码无效或订单不存在" });
          return;
        }

        this.setData({
          verifyInfo: {
            receiverName: data.receiverName || "-",
            merchantName: data.merchantName || "-",
            boxNo: data.boxNo || "-",
            statusText: data.statusText || "-",
            verifyCode: data.verifyCode || "-"
          }
        });
      },
      fail: () => {
        this.setData({ errorText: "网络异常，暂时无法加载核验信息。" });
      },
      complete: () => {
        this.setData({ loading: false });
      }
    });
  },

  goBackBySource() {
    const from = this.data.from || "";
    if (from === "courier") {
      wx.reLaunch({ url: "/pages/courier-home/courier-home" });
      return;
    }
    if (from === "user") {
      wx.reLaunch({ url: "/pages/user-home/user-home" });
      return;
    }
    wx.reLaunch({ url: "/pages/login/login" });
  }
});
