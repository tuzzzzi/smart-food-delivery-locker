function safeDecode(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  try {
    return decodeURIComponent(text);
  } catch (error) {
    return text;
  }
}

function parseQrVerifyToken(value) {
  const text = safeDecode(value);
  if (!text) return "";

  const qrPrefix = text.match(/^QR_VERIFY:\s*([A-Za-z0-9_-]+)$/i);
  if (qrPrefix && qrPrefix[1]) return qrPrefix[1];

  const verifyApi = text.match(/\/api\/order\/verify_qr\/([A-Za-z0-9_-]+)/i);
  if (verifyApi && verifyApi[1]) return verifyApi[1];

  const pageToken = text.match(/(?:^|[?&#])token=([A-Za-z0-9_-]+)/i);
  if (pageToken && pageToken[1]) return pageToken[1];

  const pureToken = text.match(/^[A-Za-z0-9_-]{8,}$/);
  if (pureToken) return text;

  return "";
}

function openVerifyPage(token, source = "") {
  const cleanToken = parseQrVerifyToken(token);
  if (!cleanToken) return false;
  const from = String(source || "").trim();
  wx.navigateTo({
    url:
      `/pages/order-verify/order-verify?token=${encodeURIComponent(cleanToken)}` +
      (from ? `&from=${encodeURIComponent(from)}` : "")
  });
  return true;
}

function scanFailTitle(error) {
  const message = String((error && (error.errMsg || error.message)) || "");
  if (/cancel/i.test(message)) return "已取消扫码";
  return "扫码未成功，请使用真机扫描二维码，或点击查看核验信息。";
}

function scanAndOpenVerify(source = "") {
  wx.scanCode({
    onlyFromCamera: false,
    scanType: ["qrCode"],
    success: (res) => {
      const rawResult = (res && res.result) || "";
      console.log("[qr-verify] scan raw result:", rawResult, res || {});

      if (!rawResult) {
        wx.showToast({ title: "扫码未成功，请使用真机扫描二维码，或点击查看核验信息。", icon: "none" });
        return;
      }

      const token = parseQrVerifyToken(rawResult);
      if (!token) {
        wx.showToast({ title: "二维码格式不正确", icon: "none" });
        return;
      }

      openVerifyPage(token, source);
    },
    fail: (error) => {
      console.warn("[qr-verify] scan failed:", error || {});
      wx.showToast({
        title: scanFailTitle(error),
        icon: "none"
      });
    }
  });
}

module.exports = {
  parseQrVerifyToken,
  openVerifyPage,
  scanAndOpenVerify
};
