function clearAuthStorage() {
  wx.removeStorageSync("openid");
  wx.removeStorageSync("token");
  wx.removeStorageSync("roles");
  wx.removeStorageSync("currentRole");
  wx.removeStorageSync("username");
  wx.removeStorageSync("abandonedTaskId");
}

Page({
  data: {},

  enterAsUser() {
    wx.setStorageSync("currentRole", "user");
    wx.navigateTo({ url: "/pages/user-home/user-home" });
  },

  enterAsCourier() {
    wx.setStorageSync("currentRole", "courier");
    wx.navigateTo({ url: "/pages/courier-home/courier-home" });
  },

  switchIdentity() {
    clearAuthStorage();
    wx.reLaunch({ url: "/pages/login/login" });
  }
});
