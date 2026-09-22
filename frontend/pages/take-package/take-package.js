const { getBaseUrl, normalizeMediaUrl } = require("../../utils/request");
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

function fmtDisplayTime(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

function packageStatusText(status) {
  if (status === "pending") return "待取件";
  if (status === "picked" || status === "completed") return "已完成";
  return status || "待同步";
}

function packageStatusClass(status) {
  if (status === "picked" || status === "completed") return "status-picked";
  return "status-pending";
}

function aiStatus(raw) {
  const status = raw.aiStatus || raw.resultImageStatus || raw.snapshotStatus || raw.recordingStatus || "";
  if (status === "ai_failed" || status === "failed") {
    return { text: "包裹信息待确认", className: "ai-fail" };
  }
  if (status === "ai_passed" || status === "passed" || raw.previewImageUrl || raw.resultImageUrl || raw.snapshotUrl) {
    return { text: "包裹已确认", className: "ai-pass" };
  }
  return { text: "待确认", className: "ai-waiting" };
}

function confidenceText(raw) {
  const value = raw.confidence || raw.aiConfidence || raw.score;
  if (value === undefined || value === null || value === "") return "";
  const num = Number(value);
  if (Number.isNaN(num)) return String(value);
  return num <= 1 ? `${Math.round(num * 100)}%` : `${Math.round(num)}%`;
}

function normalizePackage(raw) {
  return {
    ...raw,
    statusText: packageStatusText(raw.status),
    statusClass: packageStatusClass(raw.status),
    arrivedAtText: fmtTime(raw.arrivedAt),
    pickedAtText: fmtTime(raw.pickedAt)
  };
}

function normalizeEvidence(raw) {
  if (!raw) return null;
  const scene = raw.commandScene || raw.recordingScene || "";
  const snapshotUrl = normalizeMediaUrl(raw.snapshotUrl || "");
  const resultImageUrl = normalizeMediaUrl(raw.resultImageUrl || "");
  const previewImageUrl = normalizeMediaUrl(raw.previewImageUrl || raw.resultImageUrl || raw.snapshotUrl || "");
  const status = aiStatus({ ...raw, previewImageUrl, resultImageUrl, snapshotUrl });

  return {
    ...raw,
    recordingStartedAtText: fmtDisplayTime(raw.recordingStartedAt),
    recordingStoppedAtText: fmtDisplayTime(raw.recordingStoppedAt),
    previewImageLabel: raw.previewImageLabel || (scene === "pickup" ? "取件图片" : "入柜图片"),
    evidenceTypeLabel: scene === "pickup" ? "取件凭证" : "入柜凭证",
    evidenceStatusText: previewImageUrl ? "入柜凭证已生成" : "入柜凭证生成中",
    aiStatusText: status.text,
    aiStatusClass: status.className,
    confidenceText: confidenceText(raw),
    snapshotUrl,
    resultImageUrl,
    previewImageUrl
  };
}

Page({
  data: {
    entryRole: "user",
    directPackageId: "",
    pickupCode: "",
    loading: false,
    hintText: "",
    pkg: null,
    depositEvidence: null
  },

  onLoad(options) {
    const packageId = (options.packageId || "").trim();
    const entryRole = (options.role || "user").trim() || "user";
    this.setData({ entryRole, directPackageId: packageId });

    if (packageId) {
      this.loadPackageDetail(packageId);
    }
  },

  onInput(e) {
    const value = (e.detail.value || "").replace(/\D/g, "");
    this.setData({ pickupCode: value, hintText: "", pkg: null, depositEvidence: null });
  },

  submit() {
    const code = (this.data.pickupCode || "").trim();
    if (code.length !== 6) {
      this.setData({ hintText: "请输入 6 位取件码后再查询。" });
      return;
    }

    if (!getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }

    this.setData({ loading: true, hintText: "", pkg: null, depositEvidence: null });

    wx.request({
      url: `${BASE_URL}/api/user/find_by_code`,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data: { pickupCode: code },
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "未找到对应包裹。" });
          return;
        }

        this.setData({
          pkg: normalizePackage(data.package || {}),
          depositEvidence: normalizeEvidence(data.depositEvidence)
        });
        wx.showToast({ title: "查询成功", icon: "success" });
      },
      fail: () => {
        this.setData({ hintText: "网络异常，暂时无法查询取件码。" });
      },
      complete: () => {
        this.setData({ loading: false });
      }
    });
  },

  loadPackageDetail(packageId) {
    if (!packageId) return;

    if (!getToken()) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }

    const role = this.data.entryRole || "user";
    const url = role === "courier"
      ? `${BASE_URL}/api/courier/package?packageId=${encodeURIComponent(packageId)}`
      : `${BASE_URL}/api/user/package?packageId=${encodeURIComponent(packageId)}`;

    this.setData({ loading: true, hintText: "" });

    wx.request({
      url,
      method: "GET",
      header: authHeader(),
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success" || !data.package) {
          this.setData({ hintText: data.message || "无法加载包裹凭证。" });
          return;
        }

        this.setData({
          pkg: normalizePackage(data.package || {}),
          depositEvidence: normalizeEvidence(data.depositEvidence)
        });
      },
      fail: () => {
        this.setData({ hintText: "网络异常，暂时无法加载包裹凭证。" });
      },
      complete: () => {
        this.setData({ loading: false });
      }
    });
  },

  goVerifyInfo() {
    const pkg = this.data.pkg || {};
    const token = pkg.verifyToken || "";
    if (!token) {
      wx.showToast({ title: "暂无核验信息", icon: "none" });
      return;
    }
    wx.navigateTo({
      url:
        `/pages/order-verify/order-verify?token=${encodeURIComponent(token)}` +
        `&from=${encodeURIComponent(this.data.entryRole || "user")}`
    });
  },

  scanVerifyQr() {
    scanAndOpenVerify(this.data.entryRole === "courier" ? "courier" : "user");
  },

  goGuide() {
    if (!this.data.pkg || !this.data.pkg.packageId) return;

    wx.navigateTo({
      url: `/pages/guide/guide?role=user&packageId=${encodeURIComponent(this.data.pkg.packageId)}`
    });
  },

  goHome() {
    if (this.data.entryRole === "courier") {
      const pages = getCurrentPages();
      if (pages.length > 1) {
        wx.navigateBack({ delta: 1 });
        return;
      }
      wx.reLaunch({ url: "/pages/courier-home/courier-home" });
      return;
    }
    wx.reLaunch({ url: "/pages/user-home/user-home" });
  }
});
