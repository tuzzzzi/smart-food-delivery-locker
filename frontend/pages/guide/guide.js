const { getBaseUrl, normalizeMediaUrl } = require("../../utils/request");
const { scanAndOpenVerify } = require("../../utils/qr-verify");
const BASE_URL = getBaseUrl();
const AI_POLL_INTERVAL_MS = 2000;
const LOCKER_POLL_INTERVAL_MS = 1200;
const PICKUP_POLL_INTERVAL_MS = 1200;

function getToken() {
  return wx.getStorageSync("token") || wx.getStorageSync("openid") || "";
}

function authHeader() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function toRows(cells) {
  const rows = [];
  for (let i = 0; i < (cells || []).length; i += 5) {
    rows.push(cells.slice(i, i + 5));
  }
  return rows;
}

function statusToText(status) {
  if (status === "empty") return "空闲";
  if (status === "reserved") return "处理中";
  if (status === "occupied") return "占用中";
  return status || "";
}

function renderCabinet(boxCells, highlightBox, overlayStatus, overlayText) {
  const cells = (boxCells || []).map((cell) => ({
    ...cell,
    statusText: statusToText(cell.status)
  }));

  if (!highlightBox) {
    return toRows(cells);
  }

  return toRows(
    cells.map((cell) => {
      if (cell.boxNo !== highlightBox) return cell;
      return {
        ...cell,
        status: overlayStatus || cell.status,
        statusText: overlayText || cell.statusText
      };
    })
  );
}

function encodeSceneMessage(message) {
  return encodeURIComponent(message || "");
}

function formatUnixTs(ts) {
  if (!ts) return "";
  const d = new Date(Number(ts) * 1000);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

function formatDisplayTime(value) {
  if (!value) return "";
  if (typeof value === "number" || /^\d+$/.test(String(value))) {
    return formatUnixTs(value);
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value || "");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

function getPickupCodeText(pkg) {
  const value = (
    pkg &&
    (pkg.pickupCode || pkg.pickup_code || pkg.code || pkg.pickupCodeText || pkg.pickup_code_text)
  ) || "";
  return String(value || "").trim() || "暂无";
}

function normalizeEvidence(raw) {
  if (!raw) return null;
  const scene = raw.commandScene || raw.recordingScene || "";
  const snapshotUrl = normalizeMediaUrl(raw.snapshotUrl || "");
  const resultImageUrl = normalizeMediaUrl(raw.resultImageUrl || "");
  const previewImageUrl = normalizeMediaUrl(raw.previewImageUrl || raw.resultImageUrl || raw.snapshotUrl || "");
  const snapshotLabel = raw.snapshotLabel || (scene === "deposit" ? "入柜照片" : (scene === "pickup" ? "取件照片" : "现场照片"));
  const resultImageLabel = raw.resultImageLabel || (scene === "deposit" ? "入柜照片" : (scene === "pickup" ? "取件照片" : "现场照片"));
  const previewImageLabel = raw.previewImageLabel || (resultImageUrl ? resultImageLabel : snapshotLabel);
  const resultStatus = raw.resultImageStatus || "";
  const snapshotStatus = raw.snapshotStatus || "";
  const evidenceStatusText = previewImageUrl
    ? "入柜凭证已生成"
    : (resultStatus === "waiting_ai" || resultStatus === "pending" || snapshotStatus === "pending")
      ? "正在生成"
      : (resultStatus === "failed" || resultStatus === "ai_failed" || snapshotStatus === "failed")
        ? "生成失败"
        : "待确认";
  const aiStatusText = resultStatus === "ai_failed" || resultStatus === "failed"
    ? "包裹信息待确认"
    : (resultStatus === "ai_passed" || resultStatus === "passed" || previewImageUrl)
      ? "包裹已确认"
      : "待确认";
  const aiStatusClass = aiStatusText === "包裹信息待确认" ? "ai-fail" : (aiStatusText === "包裹已确认" ? "ai-pass" : "ai-waiting");
  const confidenceValue = raw.confidence || raw.aiConfidence || raw.score;
  const confidenceNum = Number(confidenceValue);
  const confidenceText = confidenceValue === undefined || confidenceValue === null || confidenceValue === ""
    ? ""
    : (Number.isNaN(confidenceNum)
      ? String(confidenceValue)
      : (confidenceNum <= 1 ? `${Math.round(confidenceNum * 100)}%` : `${Math.round(confidenceNum)}%`));
  return {
    ...raw,
    recordingStartedAtText: formatDisplayTime(raw.recordingStartedAt),
    recordingStoppedAtText: formatDisplayTime(raw.recordingStoppedAt),
    snapshotStatusLabel: raw.snapshotStatusLabel || raw.snapshotStatus || "待生成快照",
    resultImageStatusLabel: raw.resultImageStatusLabel || raw.resultImageStatus || "待生成结果图",
    videoStatusLabel: raw.videoStatusLabel || raw.videoStatus || "待生成留痕",
    recordingStatusLabel: raw.recordingStatusLabel || raw.recordingStatus || "待触发",
    snapshotLabel,
    resultImageLabel,
    previewImageLabel,
    evidenceTypeLabel: scene === "pickup" ? "取件留痕" : "入柜留痕",
    evidenceStatusText,
    aiStatusText,
    aiStatusClass,
    confidenceText,
    snapshotUrl,
    resultImageUrl,
    previewImageUrl,
  };
}

function pickupStatusText(status) {
  if (status === "pending") return "待取件";
  if (status === "picked" || status === "completed") return "已完成";
  return status || "";
}

function maskPhone(phone) {
  const value = String(phone || "").trim();
  if (!value) return "";
  if (value.length < 7) return value;
  return `${value.slice(0, 3)}****${value.slice(-4)}`;
}

function sanitizeCourierMessage(message, fallback = "") {
  const text = String(message || "").trim();
  if (!text) return fallback;
  if (/YOLO|YOLOv5|artifacts\/locker|detectorStatus|snapshot_missing|snapshot_pending|deposit|执行成功|未检测到包裹目标|未检测到目标|no object|no package/i.test(text)) {
    return fallback || "系统暂未确认包裹入柜，请稍后重试。";
  }
  return text
    .replace(/硬件闭环|业务闭环|闭环/g, "柜门状态")
    .replace(/异常处理中/g, "问题处理中");
}

function courierStatusTitle(status) {
  if (status === "ai_failed") return "未确认包裹入柜";
  if (status === "waiting_ai") return "正在检测包裹";
  if (status === "ai_passed" || status === "delivered" || status === "completed") return "包裹已确认入柜";
  if (status === "assigned" || status === "reassigned") return "待投递";
  return "任务处理中";
}

function courierStatusClass(status) {
  if (status === "ai_failed") return "chip-warn";
  if (status === "ai_passed" || status === "delivered" || status === "completed") return "chip-success";
  return "chip-blue";
}

function formatBoxLocation(boxNo) {
  const value = String(boxNo || "").trim();
  const match = value.match(/^([A-Za-z])0*(\d+)$/);
  if (!match) return value ? `${value}号柜` : "柜号待同步";
  const area = match[1].toUpperCase();
  const number = match[2].padStart(2, "0");
  return `${area}区${number}号柜`;
}

function formatUserBoxLocation(boxNo) {
  const value = String(boxNo || "").trim();
  const match = value.match(/^([A-Za-z])0*(\d+)$/);
  if (!match) return value ? `${value}号柜门` : "柜号待同步";
  return `${match[1].toUpperCase()} 区第 ${Number(match[2])} 个柜门`;
}

function formatUserPickupHint(message, fallback) {
  let text = String(message || "").trim();
  if (!text) return fallback || "";
  if (/Hardware locker command created|pending command|Waiting for ESP32|waiting.*poll|poll.*command|door sensor callbacks|command created/i.test(text)) {
    return "开柜请求已发送，正在等待柜体响应。";
  }
  if (/ESP32|offline|not online|unavailable|timeout|timed out|no callback|not responding|connection refused|hardware.*failed|hardware.*error/i.test(text)) {
    return "柜体当前响应较慢，请稍候重试；若长时间无响应，请联系管理员。";
  }
  if (/door.*closed|closed.*door|硬件闭环已完成|闭环完成|business.*completed/i.test(text)) {
    return "柜门已关闭，正在确认取件状态。";
  }
  if (/pickup.*completed|process.*completed|取件.*完成/i.test(text)) {
    return "取件流程已完成，系统正在更新记录。";
  }
  if (/YOLO|YOLOv5|detectorStatus|rawPayload|snapshotPath|outputDir|archiveDirectory|commandId|packageId|taskId|locker command|hardware|ESP32/i.test(text)) {
    return fallback || "当前取件状态正在确认，请稍候。";
  }
  if (/[A-Za-z]{4,}/.test(text)) {
    return fallback || "当前取件状态正在确认，请稍候。";
  }
  return text
    .replace(/硬件闭环已完成/g, "柜门已关闭")
    .replace(/系统正在同步取件结果/g, "正在确认取件状态")
    .replace(/闭环完成/g, "已关门")
    .replace(/业务闭环|硬件闭环|取件闭环|闭环/g, "取件状态")
    .replace(/异常处理中/g, "问题处理中");
}

function sanitizePickupMessage(message, fallback) {
  return formatUserPickupHint(message, fallback);
}

function normalizeLockerStatus(raw, fallbackBoxNo) {
  if (!raw) return null;

  if (raw.latestCommand) {
    return {
      ...raw,
      boxNo: raw.boxNo || fallbackBoxNo || ((raw.latestCommand || {}).boxNo || "")
    };
  }

  if (raw.commandId) {
    return {
      boxNo: raw.boxNo || fallbackBoxNo || "",
      latestCommand: raw,
      pendingHardware: !!raw.pendingHardware,
      hasAlert: !!raw.hasAlert,
      businessStatus: raw.businessStatus || "",
      doorState: raw.doorState || "",
      lockFeedbackState: raw.lockFeedbackState || "",
      controllerPowerState: raw.controllerPowerState || "",
      lockPowerState: raw.lockPowerState || ""
    };
  }

  return {
    ...raw,
    boxNo: raw.boxNo || fallbackBoxNo || ""
  };
}

function resolveLockerModeLabel(lockerStatus) {
  const latest = lockerStatus && lockerStatus.latestCommand ? lockerStatus.latestCommand : null;
  if (!latest) return "";

  if (latest.mode === "hardware" && !latest.fallbackUsed) {
    return "设备连接正常";
  }
  if (latest.mode === "hardware" && latest.fallbackUsed) {
    return "设备服务处理中";
  }
  if (latest.mode === "mock") {
    return "设备服务处理中";
  }
  return latest.mode || "";
}

function buildPickupHardwareHint(lockerStatus) {
  const latest = lockerStatus && lockerStatus.latestCommand ? lockerStatus.latestCommand : null;
  if (!latest) {
    return "请先确认箱体位置，再发起开门取件。";
  }
  if (latest.hasAlert) {
    return sanitizePickupMessage(latest.businessNote || latest.lastError || latest.detail, "柜门状态异常，请稍后重试或提交管理员处理。");
  }
  if (latest.businessStatus === "completed") {
    return "柜门已关闭，正在确认取件状态。";
  }
  if (latest.status === "accepted" || latest.status === "created" || latest.status === "pending") {
    return "开柜请求已发送，正在等待柜体响应。";
  }
  if (latest.status === "relay_triggered") {
    return "柜门正在打开，请稍候。";
  }
  if (latest.status === "waiting_door_open") {
    return "柜门已解锁，请拉开柜门并取出包裹。";
  }
  if (latest.businessStatus === "waiting_close" || latest.status === "door_opened") {
    return "请取出包裹，并在取出后关闭柜门。";
  }
  return sanitizePickupMessage(latest.businessNote || latest.detail, "正在确认取件状态，请稍候。");
}

function buildPickupLockerView(lockerStatus, pkg) {
  const latest = lockerStatus && lockerStatus.latestCommand ? lockerStatus.latestCommand : null;
  const modeLabel = resolveLockerModeLabel(lockerStatus);

  if (pkg && pkg.status === "picked") {
    return {
      modeLabel,
      phaseLabel: "已完成",
      phaseClass: "ok",
      phaseKey: "done",
      hint: "取件已完成。",
      primaryActionText: "返回首页",
      primaryDisabled: false,
      commandId: latest ? latest.commandId || "" : "",
      doorState: lockerStatus && lockerStatus.doorState ? lockerStatus.doorState : (latest && latest.doorState) || "",
      lockFeedbackState: lockerStatus && lockerStatus.lockFeedbackState ? lockerStatus.lockFeedbackState : (latest && latest.lockFeedbackState) || ""
    };
  }

  if (!latest) {
    return {
      modeLabel,
      phaseLabel: "待取件",
      phaseClass: "neutral",
      phaseKey: "ready",
      hint: "请确认柜号后点击下方按钮打开柜门。",
      primaryActionText: "打开柜门取件",
      primaryDisabled: false,
      commandId: "",
      doorState: "",
      lockFeedbackState: ""
    };
  }

  if (latest.hasAlert) {
    return {
      modeLabel,
      phaseLabel: "问题处理中",
      phaseClass: "danger",
      phaseKey: "error",
      hint: buildPickupHardwareHint(lockerStatus),
      primaryActionText: "查看处理进度",
      primaryDisabled: false,
      commandId: latest.commandId || "",
      doorState: lockerStatus && lockerStatus.doorState ? lockerStatus.doorState : latest.doorState || "",
      lockFeedbackState: lockerStatus && lockerStatus.lockFeedbackState ? lockerStatus.lockFeedbackState : latest.lockFeedbackState || ""
    };
  }

  if (latest.businessStatus === "completed") {
    return {
      modeLabel,
      phaseLabel: "已关门",
      phaseClass: "ok",
      phaseKey: "closed",
      hint: buildPickupHardwareHint(lockerStatus),
      primaryActionText: "确认中...",
      primaryDisabled: true,
      commandId: latest.commandId || "",
      doorState: lockerStatus && lockerStatus.doorState ? lockerStatus.doorState : latest.doorState || "",
      lockFeedbackState: lockerStatus && lockerStatus.lockFeedbackState ? lockerStatus.lockFeedbackState : latest.lockFeedbackState || ""
    };
  }

  if (latest.status === "accepted" || latest.status === "created" || latest.status === "pending" || latest.status === "relay_triggered") {
    return {
      modeLabel,
      phaseLabel: "开门中",
      phaseClass: "waiting",
      phaseKey: "opening",
      hint: buildPickupHardwareHint(lockerStatus),
      primaryActionText: "正在开门...",
      primaryDisabled: true,
      commandId: latest.commandId || "",
      doorState: lockerStatus && lockerStatus.doorState ? lockerStatus.doorState : latest.doorState || "",
      lockFeedbackState: lockerStatus && lockerStatus.lockFeedbackState ? lockerStatus.lockFeedbackState : latest.lockFeedbackState || ""
    };
  }

  if (latest.status === "waiting_door_open") {
    return {
      modeLabel,
      phaseLabel: "已开门",
      phaseClass: "waiting",
      phaseKey: "opening",
      hint: buildPickupHardwareHint(lockerStatus),
      primaryActionText: "请取出包裹",
      primaryDisabled: true,
      commandId: latest.commandId || "",
      doorState: lockerStatus && lockerStatus.doorState ? lockerStatus.doorState : latest.doorState || "",
      lockFeedbackState: lockerStatus && lockerStatus.lockFeedbackState ? lockerStatus.lockFeedbackState : latest.lockFeedbackState || ""
    };
  }

  if (latest.businessStatus === "waiting_close" || latest.status === "door_opened") {
    return {
      modeLabel,
      phaseLabel: "已开门",
      phaseClass: "processing",
      phaseKey: "waiting_close",
      hint: buildPickupHardwareHint(lockerStatus),
      primaryActionText: "请取出包裹",
      primaryDisabled: true,
      commandId: latest.commandId || "",
      doorState: lockerStatus && lockerStatus.doorState ? lockerStatus.doorState : latest.doorState || "",
      lockFeedbackState: lockerStatus && lockerStatus.lockFeedbackState ? lockerStatus.lockFeedbackState : latest.lockFeedbackState || ""
    };
  }

  return {
    modeLabel,
    phaseLabel: "处理中",
    phaseClass: "processing",
    phaseKey: "processing",
    hint: buildPickupHardwareHint(lockerStatus),
    primaryActionText: "确认中...",
    primaryDisabled: true,
    commandId: latest.commandId || "",
    doorState: lockerStatus && lockerStatus.doorState ? lockerStatus.doorState : latest.doorState || "",
    lockFeedbackState: lockerStatus && lockerStatus.lockFeedbackState ? lockerStatus.lockFeedbackState : latest.lockFeedbackState || ""
  };
}

function shouldTrackPickupHardware(lockerStatus, packageId) {
  const latest = lockerStatus && lockerStatus.latestCommand ? lockerStatus.latestCommand : null;
  if (!latest) return false;
  if (latest.packageId && latest.packageId !== packageId) return false;
  return !!(
    lockerStatus.pendingHardware ||
    latest.pendingHardware ||
    latest.businessStatus === "waiting_close" ||
    latest.status === "door_opened" ||
    latest.status === "waiting_door_open" ||
    latest.status === "accepted" ||
    latest.status === "relay_triggered"
  );
}

function buildCourierHardwareHint(lockerStatus, task) {
  const latest = lockerStatus && lockerStatus.latestCommand ? lockerStatus.latestCommand : null;
  const hasActiveException = !!(task && task.hasActiveException);
  if (!latest) {
    if (hasActiveException) {
      return task.exceptionNote || task.aiMessage || "当前任务存在未处理异常，请先确认是否继续重试或转人工处理。";
    }
    return "请先开柜，放入包裹后关好柜门。";
  }
  if (latest.hasAlert && hasActiveException) {
    return latest.businessNote || latest.lastError || latest.detail || "柜门状态异常，请确认现场情况后重试或转人工处理。";
  }
  if (latest.businessStatus === "completed") {
    return sanitizeCourierMessage(latest.businessNote, "柜门已关闭，系统正在确认入柜结果。");
  }
  if (latest.status === "accepted" || latest.status === "relay_triggered") {
    return "开门请求已发送，正在等待柜门响应。";
  }
  if (latest.status === "waiting_door_open") {
    return "柜门已解锁，请打开柜门、放入包裹并关好柜门。";
  }
  if (latest.businessStatus === "waiting_close" || latest.status === "door_opened") {
    return "柜门已打开，请放入包裹后关好柜门。";
  }
  return latest.businessNote || latest.detail || "正在同步柜门状态，请稍候。";
}

function shouldTrackCourierHardware(lockerStatus, taskId) {
  const latest = lockerStatus && lockerStatus.latestCommand ? lockerStatus.latestCommand : null;
  if (!latest) return false;
  if (latest.taskId && latest.taskId !== taskId) return false;
  if (latest.commandScene && latest.commandScene !== "deposit") return false;
  return !!(
    lockerStatus.pendingHardware ||
    latest.pendingHardware ||
    latest.businessStatus === "waiting_close" ||
    latest.status === "door_opened" ||
    latest.status === "waiting_door_open" ||
    latest.status === "accepted" ||
    latest.status === "relay_triggered" ||
    latest.status === "created"
  );
}

function buildCourierTaskView(task) {
  const taskStage = task.taskStage || {};
  const platformOrderStatus = task.platformOrderStatus || {};
  const isPlatformTask = task.source === "platform_order";
  const exceptionStatus = task.exceptionStatus || "none";
  const hasActiveException = !!task.hasActiveException;
  const exceptionResolved = !!task.exceptionResolved;
  const exceptionStatusLabel = task.exceptionStatusLabel || (hasActiveException ? "异常处理中" : (exceptionResolved ? "异常已处理完成" : ""));
  const exceptionNote = task.exceptionNote || "";
  const statusMap = {
    assigned: "待投递",
    reassigned: "待投递",
    waiting_ai: "包裹确认中",
    ai_failed: "包裹信息待确认",
    ai_passed: "包裹已确认",
    delivered: "已完成",
    completed: "已完成",
    pending: "异常处理中",
    resolved: "已处理完成",
    cancelled: "已关闭"
  };

  let nextStepText = "请根据当前任务提示继续处理。";
  let primaryActionText = "";
  let canSubmit = false;
  let canResumeAi = false;
  let canConfirmReturn = false;

  if (task.status === "assigned" || task.status === "reassigned") {
    nextStepText = `前往柜号 ${task.boxNo || "-"}，打开柜门，放入包裹并关闭柜门。`;
    primaryActionText = task.status === "reassigned" ? "重新投递" : "开始投递";
    canSubmit = true;
  } else if (task.status === "waiting_ai") {
    nextStepText = "柜门已关闭，系统正在确认包裹，请等待结果。";
    primaryActionText = "继续确认";
    canResumeAi = true;
  } else if (task.status === "ai_failed") {
    nextStepText = "包裹信息待确认，请检查包裹摆放后重新投递。";
    primaryActionText = "重新投递";
    canSubmit = true;
  } else if (task.status === "ai_passed" || task.status === "delivered" || task.status === "completed") {
    nextStepText = "包裹已确认，已完成入柜。";
    canConfirmReturn = true;
  } else if (task.status === "cancelled") {
    nextStepText = "该任务已关闭，请返回工作台查看其他任务。";
  }

  if (hasActiveException) {
    nextStepText = sanitizeCourierMessage(exceptionNote || task.aiMessage, "当前任务仍有未处理异常，请先确认是否重新投递或提交管理员处理。");
  } else if (exceptionResolved) {
    nextStepText = exceptionNote || "原异常已处理完成，当前任务已回到正常流程。";
  }
  nextStepText = sanitizeCourierMessage(nextStepText);

  const failureReasonText = task.status === "ai_failed"
    ? "系统已完成图片检测，但暂未确认包裹入柜。"
    : sanitizeCourierMessage(exceptionNote || task.aiMessage || "");
  const actionAdviceText = task.status === "ai_failed"
    ? "请重新打开柜门，调整包裹摆放后再次关门检测；如现场无法继续，可提交管理员处理。"
    : nextStepText;

  return {
    ...task,
    displayTitle: (task.merchantName || "").trim() || (isPlatformTask ? "配送任务" : "临时投递"),
    sourceText: isPlatformTask ? "平台任务" : "临时任务",
    sourceClass: isPlatformTask ? "platform" : "manual",
    receiverPhoneText: maskPhone(task.receiverPhone) || "未填写手机号",
    statusText: statusMap[task.status] || task.status || "状态同步中",
    stageLabel: taskStage.label || statusMap[task.status] || "任务处理中",
    stageDesc: taskStage.description || "",
    orderStatusLabel: platformOrderStatus.label || "",
    orderStatusDesc: platformOrderStatus.description || "",
    exceptionStatus,
    exceptionStatusLabel,
    exceptionNote,
    hasActiveException,
    exceptionResolved,
    nextStepText,
    primaryActionText,
    canSubmit,
    canResumeAi,
    canConfirmReturn,
    arrivedHint: task.status === "waiting_ai" ? "已提交入柜，正在检测。" : "",
    courierStatusTitle: courierStatusTitle(task.status),
    courierStatusClass: courierStatusClass(task.status),
    failureReasonText,
    actionAdviceText,
    boxLocationText: formatBoxLocation(task.boxNo)
  };
}

function buildCourierSteps(task, polling) {
  const status = task ? task.status : "";
  const receiveState = task ? "done" : "current";
  let openState = "pending";
  let putInState = "pending";
  let closeState = "pending";
  let aiState = "pending";
  let finishState = "pending";

  if (status === "assigned" || status === "reassigned") {
    openState = "current";
  } else if (status === "ai_failed") {
    openState = "done";
    putInState = "done";
    closeState = "done";
    aiState = "alert";
  } else if (status === "waiting_ai") {
    openState = "done";
    putInState = "done";
    closeState = "done";
    aiState = polling ? "current" : "current";
  } else if (status === "ai_passed" || status === "delivered" || status === "completed") {
    openState = "done";
    putInState = "done";
    closeState = "done";
    aiState = "done";
    finishState = "done";
  } else if (status === "cancelled") {
    openState = "done";
    putInState = "done";
    closeState = "done";
    aiState = "alert";
    finishState = "alert";
  }

  return [
    {
      key: "receive",
      label: "接单",
      desc: task && task.boxNo ? `已分配柜号 ${task.boxNo}` : "等待系统分配柜号",
      state: receiveState
    },
    {
      key: "open",
      label: "开柜",
      desc: "到达柜前后发起开柜",
      state: openState
    },
    {
      key: "put_in",
      label: "入柜",
      desc: "确认包裹放入对应柜格",
      state: putInState
    },
    {
      key: "close",
      label: "关门",
      desc: "关门后系统继续检测",
      state: closeState
    },
    {
      key: "ai",
      label: "确认",
      desc: polling ? "确认中" : "等待包裹确认",
      state: aiState
    },
    {
      key: "finish",
      label: "完成",
      desc: "投递完成并返回工作台",
      state: finishState
    }
  ];
}

function buildUserSteps(pkg, lockerView) {
  const stepKey = lockerView ? lockerView.phaseKey : "ready";
  const isPicked = pkg && pkg.status === "picked";
  const isClosed = stepKey === "closed";
  const doorActive = stepKey === "opening" || stepKey === "processing" || stepKey === "waiting_close";

  return [
    {
      key: "confirm",
      label: "确认柜号",
      desc: pkg && pkg.boxNo ? `柜号 ${pkg.boxNo}` : "确认包裹位置",
      state: "done"
    },
    {
      key: "open",
      label: "打开柜门",
      desc: "发起开柜请求",
      state: isPicked ? "done" : (stepKey === "ready" ? "current" : "done")
    },
    {
      key: "take",
      label: "取出包裹",
      desc: "取出后请及时关闭柜门",
      state: (isPicked || isClosed)
        ? "done"
        : (doorActive ? "current" : (stepKey === "error" ? "alert" : "pending"))
    },
    {
      key: "close",
      label: "关闭柜门",
      desc: "关闭后确认取件状态",
      state: (isPicked || isClosed)
        ? "done"
        : (stepKey === "waiting_close" ? "current" : (stepKey === "error" ? "alert" : "pending"))
    },
    {
      key: "finish",
      label: "完成取件",
      desc: "返回首页查看记录",
      state: isPicked ? "done" : (isClosed ? "current" : (stepKey === "error" ? "alert" : "pending"))
    }
  ];
}

function shouldStopAiPolling(task) {
  if (!task) return false;
  if (task.hasActiveException) return true;
  const status = task.status || "";
  return !!status && status !== "waiting_ai";
}

function isRetryableAiPending(data) {
  const integration = data && data.integration ? data.integration : {};
  const detectorStatus = (data && data.detectorStatus) || integration.detectorStatus || "";
  return !!(data && (data.retryable || integration.retryable || detectorStatus === "snapshot_pending"));
}

Page({
  data: {
    role: "",
    taskId: "",
    packageId: "",
    task: null,
    pkg: null,
    depositEvidence: null,
    boxCells: [],
    cabinetRows: [],
    highlightBox: "",
    hasSubmittedPutIn: false,
    loadingAction: false,
    polling: false,
    aiVerifyInFlight: false,
    aiSnapshotRetryCount: 0,
    aiSnapshotRetryMax: 5,
    pollTimer: null,
    pollCount: 0,
    pollMax: 10,
    courierLockerPending: false,
    courierPollTimer: null,
    courierPollCount: 0,
    courierPollMax: 20,
    pickupPolling: false,
    pickupPollTimer: null,
    pickupPollCount: 0,
    pickupPollMax: 20,
    pickupPendingHardware: false,
    lockerStatus: null,
    lockerView: null,
    guideSteps: [],
    showCourierEvidence: false,
    showCabinetGrid: false,
    currentBoxLocationText: "",
    canOpenPickup: false,
    hintText: "",
    pageTitle: "",
    pageSubTitle: "",
    pageOptions: null
  },

  onLoad(options) {
    const role = options.role || "";
    const taskId = options.taskId || "";
    const packageId = options.packageId || "";

    this.setData({
      role,
      taskId,
      packageId,
      pageOptions: { ...options },
      task: null,
      pkg: null,
      depositEvidence: null,
      boxCells: [],
      cabinetRows: [],
      highlightBox: "",
      hasSubmittedPutIn: false,
      loadingAction: false,
      polling: false,
      aiVerifyInFlight: false,
      aiSnapshotRetryCount: 0,
      pollTimer: null,
      pollCount: 0,
      courierPollMax: 20,
      courierLockerPending: false,
      courierPollTimer: null,
      courierPollCount: 0,
      pickupPolling: false,
      pickupPollTimer: null,
      pickupPollCount: 0,
      pickupPendingHardware: false,
      lockerStatus: null,
      lockerView: null,
      guideSteps: [],
      showCourierEvidence: false,
      showCabinetGrid: false,
      currentBoxLocationText: "",
      canOpenPickup: false,
      hintText: "",
      pageTitle: role === "courier" ? "智能投递流程" : "取件流程引导",
      pageSubTitle: role === "courier"
        ? "按步骤完成开柜、入柜和检测。"
        : "确认柜号后打开柜门，取出包裹并关闭柜门。"
    });
    console.log("[guide:onLoad]", { options, role, taskId, packageId });

    this.fetchBoxes(() => {
      if (role === "courier" && taskId) {
        this.fetchTask();
        return;
      }
      if (role === "user" && packageId) {
        this.fetchPackage();
        return;
      }
      this.setData({ hintText: "缺少业务参数，请从首页重新进入。" });
    });
  },

  toggleCourierEvidence() {
    this.setData({ showCourierEvidence: !this.data.showCourierEvidence });
  },

  toggleCabinetGrid() {
    this.setData({ showCabinetGrid: !this.data.showCabinetGrid });
  },

  onUnload() {
    this.stopPolling();
    this.stopCourierLockerPolling();
    this.stopPickupPolling();

    if (this.data.role !== "courier" || !this.data.taskId) return;
    if (this.data.hasSubmittedPutIn) return;

    const status = (this.data.task && this.data.task.status) || "";
    if (status === "ai_passed" || status === "delivered" || status === "waiting_ai") return;
    wx.setStorageSync("abandonedTaskId", this.data.taskId);
  },

  buildViewState(nextData, overrideHintText) {
    const role = nextData.role !== undefined ? nextData.role : this.data.role;
    const task = nextData.task !== undefined ? nextData.task : this.data.task;
    const pkg = nextData.pkg !== undefined ? nextData.pkg : this.data.pkg;
    const lockerStatus = nextData.lockerStatus !== undefined ? nextData.lockerStatus : this.data.lockerStatus;
    const boxCells = nextData.boxCells !== undefined ? nextData.boxCells : this.data.boxCells;
    const polling = nextData.polling !== undefined ? nextData.polling : this.data.polling;

    let highlightBox = "";
    let overlayStatus = "";
    let overlayText = "";
    let lockerView = null;
    let guideSteps = [];
    let currentBoxLocationText = "";
    let canOpenPickup = false;
    let hintText = overrideHintText;

    if (role === "courier" && task) {
      highlightBox = task.boxNo || "";
      if (task.status === "assigned" || task.status === "reassigned") {
        overlayStatus = "reserved";
        overlayText = "待投放";
      } else if (task.status === "waiting_ai") {
        overlayStatus = "reserved";
        overlayText = "确认中";
      } else if (task.status === "ai_failed") {
        overlayStatus = "reserved";
        overlayText = "待重新确认";
      } else if (task.status === "ai_passed" || task.status === "delivered") {
        overlayStatus = "occupied";
        overlayText = "已入柜";
      }

      const latestLocker = lockerStatus && lockerStatus.latestCommand ? lockerStatus.latestCommand : null;
      if (latestLocker && (!latestLocker.commandScene || latestLocker.commandScene === "deposit")) {
        if (latestLocker.status === "accepted" || latestLocker.status === "relay_triggered") {
          overlayStatus = "reserved";
          overlayText = "待开门";
        } else if (latestLocker.status === "waiting_door_open") {
          overlayStatus = "reserved";
          overlayText = "待拉门";
        } else if (latestLocker.businessStatus === "waiting_close" || latestLocker.status === "door_opened") {
          overlayStatus = "reserved";
          overlayText = "待关门";
        }
      }

      guideSteps = buildCourierSteps(task, polling);
      if (hintText === undefined) {
        hintText = sanitizeCourierMessage(buildCourierHardwareHint(lockerStatus, task) || task.nextStepText || "");
      }
    }

    if (role === "user" && pkg) {
      highlightBox = pkg.boxNo || "";
      lockerView = buildPickupLockerView(lockerStatus, pkg);
      if (pkg.status === "picked") {
        overlayStatus = "empty";
        overlayText = "已完成";
      } else if (lockerView && lockerView.phaseKey === "closed") {
        overlayStatus = "reserved";
        overlayText = "确认中";
      } else if (lockerView && lockerView.phaseKey === "waiting_close") {
        overlayStatus = "reserved";
        overlayText = "待关门";
      } else if (lockerView && (lockerView.phaseKey === "opening" || lockerView.phaseKey === "processing")) {
        overlayStatus = "reserved";
        overlayText = "处理中";
      } else {
        overlayStatus = "reserved";
        overlayText = "待取件";
      }

      guideSteps = buildUserSteps(pkg, lockerView);
      currentBoxLocationText = formatUserBoxLocation(pkg.boxNo);
      canOpenPickup = pkg.status === "pending" && !!(pkg.packageId || this.data.packageId);
      if (hintText === undefined) {
        hintText = (lockerView && lockerView.hint) || "";
      }
    }

    return {
      highlightBox,
      cabinetRows: renderCabinet(boxCells, highlightBox, overlayStatus, overlayText),
      lockerView,
      guideSteps,
      currentBoxLocationText,
      canOpenPickup,
      hintText: hintText === undefined ? this.data.hintText : hintText
    };
  },

  fetchBoxes(done) {
    wx.request({
      url: `${BASE_URL}/api/boxes`,
      method: "GET",
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "获取箱体失败" });
          done && done();
          return;
        }

        const boxCells = (data.boxes || []).map((box) => ({
          boxNo: box.boxNo,
          status: box.status
        }));
        const viewState = this.buildViewState({ boxCells }, this.data.hintText);

        this.setData({
          boxCells,
          ...viewState
        });
        done && done();
      },
      fail: () => {
        this.setData({ hintText: "网络错误：无法获取箱体状态。" });
        done && done();
      }
    });
  },

  fetchTask(done) {
    wx.request({
      url: `${BASE_URL}/api/courier/task?taskId=${encodeURIComponent(this.data.taskId)}`,
      method: "GET",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success" || !data.task) {
          this.setData({ hintText: data.message || "获取任务失败，请返回工作台刷新。" });
          done && done();
          return;
        }

        const task = buildCourierTaskView(data.task);
        const lockerStatus = normalizeLockerStatus(data.hardware, task.boxNo);
        const depositEvidence = normalizeEvidence(data.depositEvidence);
        const viewState = this.buildViewState({
          task,
          lockerStatus
        });

        this.setData({
          task,
          depositEvidence,
          lockerStatus,
          ...viewState
        });
        done && done();
      },
      fail: () => {
        this.setData({ hintText: "网络错误：无法获取任务详情。" });
        done && done();
      }
    });
  },

  fetchPackage(done) {
    wx.request({
      url: `${BASE_URL}/api/user/package?packageId=${encodeURIComponent(this.data.packageId)}`,
      method: "GET",
      header: authHeader(),
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success" || !data.package) {
          this.setData({ hintText: data.message || "获取包裹失败，请返回首页刷新。" });
          done && done();
          return;
        }

        const pkg = {
          ...data.package,
          statusText: pickupStatusText(data.package.status),
          pickupCodeText: getPickupCodeText(data.package),
          arrivedAtText: formatUnixTs(data.package.arrivedAt),
          pickedAtText: formatUnixTs(data.package.pickedAt)
        };
        const lockerStatus = normalizeLockerStatus(data.hardware, pkg.boxNo);
        const depositEvidence = normalizeEvidence(data.depositEvidence);
        const keepTracking = pkg.status === "pending" && shouldTrackPickupHardware(lockerStatus, pkg.packageId);
        const viewState = this.buildViewState({
          pkg,
          lockerStatus,
          pickupPendingHardware: keepTracking
        });

        this.setData({
          pkg,
          depositEvidence,
          lockerStatus,
          pickupPendingHardware: keepTracking,
          ...viewState
        });

        if (keepTracking) {
          this.startPickupPolling();
        } else if (pkg.status === "picked") {
          this.stopPickupPolling();
        }
        done && done();
      },
      fail: () => {
        this.setData({ hintText: "网络错误：无法获取包裹详情。" });
        done && done();
      }
    });
  },

  confirmPutInBoxAndStartPolling() {
    this.setData({ loadingAction: true, hintText: "", hasSubmittedPutIn: true });

    wx.request({
      url: `${BASE_URL}/api/courier/open_locker_for_task`,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data: { taskId: this.data.taskId },
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          wx.showToast({ title: data.message || "提交失败", icon: "none" });
          this.setData({ hasSubmittedPutIn: false });
          return;
        }

        if (data.pendingHardware) {
          const nextTask = data.task ? buildCourierTaskView(data.task) : this.data.task;
          const depositEvidence = normalizeEvidence(data.depositEvidence);
          const lockerStatus = normalizeLockerStatus(
            data.hardware || (data.integration && data.integration.locker),
            (nextTask && nextTask.boxNo) || (this.data.task && this.data.task.boxNo) || ""
          );
          const hintText = sanitizeCourierMessage(
            buildCourierHardwareHint(lockerStatus, nextTask),
            "开门请求已发送，请按提示完成开门、放件和关门。"
          );
          const viewState = this.buildViewState({
            task: nextTask,
            lockerStatus
          }, hintText);

          this.setData({
            task: nextTask,
            depositEvidence,
            lockerStatus,
            courierLockerPending: true,
            ...viewState
          });
          this.refreshCourierTaskAndBoxes(() => {
            this.startCourierLockerPolling();
          });
          wx.showToast({ title: "开柜请求已发送", icon: "none" });
          return;
        }

        wx.showToast({ title: "入柜已确认", icon: "success" });
        this.refreshCourierTaskAndBoxes(() => {
          this.startPolling();
        });
      },
      fail: () => {
        wx.showToast({ title: "请求失败", icon: "none" });
        this.setData({ hasSubmittedPutIn: false });
      },
      complete: () => {
        this.setData({ loadingAction: false });
      }
    });
  },

  resumeAiPolling() {
    if (this.data.polling) return;
    this.startPolling();
  },

  openDeliveryError(scene, message) {
    const url =
      `/pages/delivery-error/delivery-error?role=${encodeURIComponent(this.data.role || "")}` +
      `&scene=${encodeURIComponent(scene || "manual")}` +
      `&taskId=${encodeURIComponent(this.data.taskId || "")}` +
      `&packageId=${encodeURIComponent(this.data.packageId || "")}` +
      `&message=${encodeSceneMessage(message)}`;

    wx.navigateTo({ url });
  },

  refreshCourierTaskAndBoxes(done) {
    this.fetchBoxes(() => {
      this.fetchTask(() => {
        done && done();
      });
    });
  },

  startAiPollingV2() {
    if (this.data.polling) return;

    this.stopCourierLockerPolling();
    this.setData({
      polling: true,
      pollCount: 0,
      aiVerifyInFlight: false,
      aiSnapshotRetryCount: 0,
      hintText: "系统正在确认包裹，请稍候"
    });
    const viewState = this.buildViewState({ polling: true });
    this.setData(viewState);

    const tick = () => {
      if (shouldStopAiPolling(this.data.task)) {
        this.stopPolling();
        return;
      }

      if (this.data.aiVerifyInFlight) {
        return;
      }

      const nextCount = this.data.pollCount + 1;
      this.setData({ pollCount: nextCount, aiVerifyInFlight: true });

      wx.request({
        url: `${BASE_URL}/api/ai/verify_box`,
        method: "POST",
        header: {
          "content-type": "application/json",
          ...authHeader()
        },
        data: { taskId: this.data.taskId },
        success: (res) => {
          const data = res.data || {};

          if (isRetryableAiPending(data)) {
            const retryCount = this.data.aiSnapshotRetryCount + 1;
            const retryMax = this.data.aiSnapshotRetryMax || 5;
            const depositEvidence = normalizeEvidence(data.depositEvidence);
            const waitHint = retryCount >= retryMax
              ? "暂未获取到入柜照片，请重新确认柜门状态或提交管理员处理。"
              : "系统正在确认包裹，请稍候";

            this.setData({
              aiSnapshotRetryCount: retryCount,
              depositEvidence: depositEvidence || this.data.depositEvidence,
              hasSubmittedPutIn: true,
              hintText: waitHint
            });

            if (retryCount >= retryMax) {
              this.setData({ hasSubmittedPutIn: false });
              this.stopPolling();
              wx.showToast({ title: "暂未获取入柜照片", icon: "none" });
              this.openDeliveryError("snapshot_pending_timeout", waitHint);
            }
            return;
          }

          if (data.status === "success" && data.package) {
            const pkg = {
              ...data.package,
              statusText: pickupStatusText(data.package.status),
              arrivedAtText: formatUnixTs(data.package.arrivedAt),
              pickedAtText: formatUnixTs(data.package.pickedAt)
            };
            const depositEvidence = normalizeEvidence(data.depositEvidence);

            const successHint = `入柜已确认，取件码 ${pkg.pickupCode} 已生成。`;
            wx.showToast({ title: "入柜已确认", icon: "success" });
            this.stopPolling();
            this.refreshCourierTaskAndBoxes(() => {
              const latestDepositEvidence = this.data.depositEvidence || depositEvidence;
              const refreshedViewState = this.buildViewState({ pkg }, successHint);
              this.setData({
                pkg,
                depositEvidence: latestDepositEvidence,
                hasSubmittedPutIn: true,
                aiSnapshotRetryCount: 0,
                ...refreshedViewState
              });
            });
            return;
          }

          if (data.status === "error") {
            this.stopPolling();
            this.refreshCourierTaskAndBoxes(() => {
              const latestTaskStatus = (this.data.task && this.data.task.status) || "";
              let failureHint = sanitizeCourierMessage(data.message, "系统暂未确认包裹入柜，请稍后重试。");

              if (latestTaskStatus === "waiting_ai") {
                failureHint = "柜门已关闭，系统正在确认包裹。";
              } else if (latestTaskStatus === "ai_passed" || latestTaskStatus === "delivered") {
                failureHint = "任务已经确认完成，可以返回工作台。";
              }

              const refreshedViewState = this.buildViewState({}, failureHint);
              this.setData({
                hasSubmittedPutIn: latestTaskStatus === "waiting_ai",
                ...refreshedViewState
              });
              wx.showToast({
                title: latestTaskStatus === "ai_failed" ? "包裹待确认" : "状态已同步",
                icon: "none"
              });
            });
            return;
          }

          if (nextCount >= this.data.pollMax) {
            this.setData({ hasSubmittedPutIn: false });
            this.stopPolling();
            wx.showToast({ title: "确认超时", icon: "none" });
            this.openDeliveryError("ai_timeout", "系统确认超时，请检查当前任务。");
          }
        },
        fail: () => {
          if (nextCount >= this.data.pollMax) {
            this.setData({ hasSubmittedPutIn: false });
            this.stopPolling();
            wx.showToast({ title: "网络不稳定", icon: "none" });
            this.openDeliveryError("network_error", "网络异常，系统确认被中断。");
          }
        },
        complete: () => {
          this.setData({ aiVerifyInFlight: false });
        }
      });
    };

    tick();
    const timer = setInterval(tick, AI_POLL_INTERVAL_MS);
    this.setData({ pollTimer: timer });
  },

  startCourierLockerPolling() {
    if (this.data.courierPollTimer || !this.data.taskId) return;

    this.setData({
      courierLockerPending: true,
      courierPollCount: 0
    });

    const tick = () => {
      const nextCount = this.data.courierPollCount + 1;
      this.setData({ courierPollCount: nextCount });

      wx.request({
        url: `${BASE_URL}/api/courier/task?taskId=${encodeURIComponent(this.data.taskId)}`,
        method: "GET",
        header: {
          "content-type": "application/json",
          ...authHeader()
        },
        success: (res) => {
          const data = res.data || {};
          if (data.status !== "success" || !data.task) {
            if (nextCount >= this.data.courierPollMax) {
              this.stopCourierLockerPolling();
              this.setData({ hasSubmittedPutIn: false });
              this.openDeliveryError("courier_hardware_timeout", data.message || "柜门状态同步超时，请人工确认现场情况。");
            }
            return;
          }

          const task = buildCourierTaskView(data.task);
          const lockerStatus = normalizeLockerStatus(data.hardware, task.boxNo);
          const latest = lockerStatus && lockerStatus.latestCommand ? lockerStatus.latestCommand : null;
          const keepTracking = shouldTrackCourierHardware(lockerStatus, task.taskId);
          const hintText = sanitizeCourierMessage(buildCourierHardwareHint(lockerStatus, task) || task.nextStepText || "");
          const viewState = this.buildViewState({
            task,
            lockerStatus
          }, hintText);

          this.setData({
            task,
            lockerStatus,
            courierLockerPending: keepTracking,
            ...viewState
          });

          if (latest && latest.hasAlert && task.hasActiveException) {
            const message = latest.businessNote || latest.lastError || latest.detail || "柜门状态异常，请人工确认现场情况。";
            this.stopCourierLockerPolling();
            this.setData({ hasSubmittedPutIn: false, hintText: message });
            wx.showToast({ title: "柜门异常", icon: "none" });
            this.openDeliveryError("courier_locker_exception", message);
            return;
          }

          if (task.status === "waiting_ai") {
            this.stopCourierLockerPolling();
            this.refreshCourierTaskAndBoxes(() => {
              this.startPolling();
            });
            return;
          }

          if (!keepTracking) {
            this.stopCourierLockerPolling();
            return;
          }

          if (nextCount >= this.data.courierPollMax) {
            this.stopCourierLockerPolling();
            this.setData({ hasSubmittedPutIn: false });
            this.openDeliveryError("courier_hardware_timeout", "开门请求已发送，但柜门状态暂未确认，请人工确认。");
          }
        },
        fail: () => {
          if (nextCount >= this.data.courierPollMax) {
            this.stopCourierLockerPolling();
            this.setData({ hasSubmittedPutIn: false });
            this.openDeliveryError("network_error", "等待柜门状态时出现网络异常。");
          }
        }
      });
    };

    tick();
    const timer = setInterval(tick, LOCKER_POLL_INTERVAL_MS);
    this.setData({ courierPollTimer: timer });
  },

  stopCourierLockerPolling() {
    if (this.data.courierPollTimer) {
      clearInterval(this.data.courierPollTimer);
    }
    this.setData({
      courierLockerPending: false,
      courierPollTimer: null,
      courierPollCount: 0
    });
  },

  startPolling() {
    return this.startAiPollingV2();

    if (this.data.polling) return;

    this.stopCourierLockerPolling();
    this.setData({ polling: true, pollCount: 0 });
    const viewState = this.buildViewState({ polling: true });
    this.setData(viewState);

    const tick = () => {
      const nextCount = this.data.pollCount + 1;
      this.setData({ pollCount: nextCount });

      wx.request({
        url: `${BASE_URL}/api/ai/verify_box`,
        method: "POST",
        header: {
          "content-type": "application/json",
          ...authHeader()
        },
        data: { taskId: this.data.taskId },
        success: (res) => {
          const data = res.data || {};

          if (data.status === "success" && data.package) {
            const pkg = {
              ...data.package,
              statusText: pickupStatusText(data.package.status),
              arrivedAtText: formatUnixTs(data.package.arrivedAt),
              pickedAtText: formatUnixTs(data.package.pickedAt)
            };

            wx.showToast({ title: "入柜已确认", icon: "success" });
            this.stopPolling();
            this.fetchBoxes(() => {
              this.fetchTask();
              this.setData({
                pkg,
                hintText: `入柜已确认，已生成取件码 ${pkg.pickupCode}，可以返回工作台。`
              });
            });
            return;
          }

          if (data.status === "error") {
            this.setData({ hasSubmittedPutIn: false });
            this.stopPolling();
            this.fetchTask();
            const message = sanitizeCourierMessage(data.message, "系统已完成图片检测，但暂未确认包裹入柜。");
            wx.showToast({ title: "暂未确认入柜", icon: "none" });
            this.openDeliveryError("ai_failed", message);
            return;
          }

          if (nextCount >= this.data.pollMax) {
            this.setData({ hasSubmittedPutIn: false });
            this.stopPolling();
            wx.showToast({ title: "确认超时", icon: "none" });
            this.openDeliveryError("ai_timeout", "系统确认超时，请人工确认当前任务。");
          }
        },
        fail: () => {
          if (nextCount >= this.data.pollMax) {
            this.setData({ hasSubmittedPutIn: false });
            this.stopPolling();
            wx.showToast({ title: "网络不稳定", icon: "none" });
            this.openDeliveryError("network_error", "网络异常，未能完成系统确认。");
          }
        }
      });
    };

    tick();
    const timer = setInterval(tick, AI_POLL_INTERVAL_MS);
    this.setData({ pollTimer: timer });
  },

  stopPolling() {
    if (this.data.pollTimer) {
      clearInterval(this.data.pollTimer);
    }
    const viewState = this.buildViewState({ polling: false });
    this.setData({
      polling: false,
      aiVerifyInFlight: false,
      pollTimer: null,
      ...viewState
    });
  },

  startPickupPolling() {
    if (this.data.pickupPolling || !this.data.packageId) return;

    this.setData({ pickupPolling: true, pickupPollCount: 0, pickupPendingHardware: true });

    const tick = () => {
      const nextCount = this.data.pickupPollCount + 1;
      this.setData({ pickupPollCount: nextCount });

      wx.request({
        url: `${BASE_URL}/api/user/package?packageId=${encodeURIComponent(this.data.packageId)}`,
        method: "GET",
        header: authHeader(),
        success: (res) => {
          const data = res.data || {};
          if (data.status !== "success" || !data.package) {
            if (nextCount >= this.data.pickupPollMax) {
              this.stopPickupPolling();
              this.setData({ pickupPendingHardware: false });
              this.openDeliveryError("hardware_timeout", sanitizePickupMessage(data.message, "柜门状态确认超时，请人工确认当前取件状态。"));
            }
            return;
          }

          const pkg = {
            ...data.package,
            statusText: pickupStatusText(data.package.status),
            pickupCodeText: getPickupCodeText(data.package),
            arrivedAtText: formatUnixTs(data.package.arrivedAt),
            pickedAtText: formatUnixTs(data.package.pickedAt)
          };
          const lockerStatus = normalizeLockerStatus(data.hardware, pkg.boxNo);
          const depositEvidence = normalizeEvidence(data.depositEvidence);
          const keepTracking = pkg.status === "pending" && shouldTrackPickupHardware(lockerStatus, pkg.packageId);
          const viewState = this.buildViewState({
            pkg,
            lockerStatus,
            pickupPendingHardware: keepTracking
          });

          this.setData({
            pkg,
            depositEvidence,
            lockerStatus,
            pickupPendingHardware: keepTracking,
            ...viewState
          });

          const latest = lockerStatus && lockerStatus.latestCommand ? lockerStatus.latestCommand : null;
          if (pkg.status === "picked") {
            this.stopPickupPolling();
            this.setData({ pickupPendingHardware: false, hintText: "取件已完成。" });
            wx.showToast({ title: "取件完成", icon: "success" });
            const pages = getCurrentPages();
            const prev = pages[pages.length - 2];
            if (prev && prev.setData) {
              prev.setData({ needRefresh: true });
            }
            return;
          }

          if (latest && latest.hasAlert) {
            const message = sanitizePickupMessage(latest.businessNote || latest.lastError || latest.detail, "柜门状态异常，请提交管理员处理。");
            this.stopPickupPolling();
            this.setData({ pickupPendingHardware: false, hintText: message });
            wx.showToast({ title: "柜门异常", icon: "none" });
            this.openDeliveryError("pickup_hardware_exception", message);
            return;
          }

          if (nextCount >= this.data.pickupPollMax) {
            this.stopPickupPolling();
            this.setData({ pickupPendingHardware: false });
            this.openDeliveryError("hardware_timeout", "开门请求已发送，但柜门状态暂未确认，请人工确认当前取件状态。");
          }
        },
        fail: () => {
          if (nextCount >= this.data.pickupPollMax) {
            this.stopPickupPolling();
            this.setData({ pickupPendingHardware: false });
            this.openDeliveryError("network_error", "网络异常，暂时无法确认取件状态。");
          }
        }
      });
    };

    tick();
    const timer = setInterval(tick, PICKUP_POLL_INTERVAL_MS);
    this.setData({ pickupPollTimer: timer });
  },

  stopPickupPolling() {
    if (this.data.pickupPollTimer) {
      clearInterval(this.data.pickupPollTimer);
    }
    this.setData({ pickupPolling: false, pickupPollTimer: null, pickupPollCount: 0 });
  },

  openDoor() {
    if (!this.data.pkg) {
      console.log("[guide:openDoor] blocked: missing pkg", {
        options: this.data.pageOptions,
        packageId: this.data.packageId,
        taskId: this.data.taskId
      });
      return;
    }

    const token = getToken();
    if (!token) {
      wx.showToast({ title: "未登录，请先登录", icon: "none" });
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }

    const packageId = this.data.pkg.packageId || this.data.packageId || "";
    const boxNo = this.data.pkg.boxNo || "";
    const requestUrl = `${BASE_URL}/api/user/open_door_by_package`;

    console.log("[guide:openDoor] request", {
      options: this.data.pageOptions,
      packageId,
      taskId: this.data.taskId,
      boxNo,
      url: requestUrl
    });

    if (!packageId) {
      wx.showToast({ title: "缺少包裹信息，请返回首页重试", icon: "none" });
      return;
    }

    this.setData({ loadingAction: true });

    wx.request({
      url: requestUrl,
      method: "POST",
      header: {
        "content-type": "application/json",
        ...authHeader()
      },
      data: {
        packageId
      },
      success: (res) => {
        const data = res.data || {};
        console.log("[guide:openDoor] response", {
          statusCode: res.statusCode,
          packageId,
          taskId: this.data.taskId,
          boxNo,
          data
        });
        if (data.status !== "success") {
          const message = formatUserPickupHint(data.message, "开柜失败，请稍后重试或联系管理员。");
          wx.showToast({ title: "开柜失败，请稍后重试", icon: "none" });
          this.openDeliveryError("open_door_failed", message);
          return;
        }

        if (data.pendingHardware) {
          const lockerStatus = normalizeLockerStatus(data.hardware || (data.integration && data.integration.locker), this.data.pkg.boxNo);
          const depositEvidence = normalizeEvidence(data.depositEvidence);
          const viewState = this.buildViewState({
            lockerStatus,
            pickupPendingHardware: true
          });

          this.setData({
            depositEvidence,
            lockerStatus,
            pickupPendingHardware: true,
            ...viewState
          });
          this.fetchPackage(() => {
            this.startPickupPolling();
          });
          wx.showToast({ title: "已下发开门指令", icon: "none" });
          return;
        }

        const pkg = {
          ...this.data.pkg,
          status: "picked",
          statusText: pickupStatusText("picked"),
          pickedAtText: formatUnixTs(Date.now() / 1000)
        };
        const viewState = this.buildViewState({ pkg, pickupPendingHardware: false }, "取件已完成。");

        this.setData({
          pkg,
          pickupPendingHardware: false,
          ...viewState
        });
        wx.showToast({ title: "取件完成", icon: "success" });
        const pages = getCurrentPages();
        const prev = pages[pages.length - 2];
        if (prev && prev.setData) {
          prev.setData({ needRefresh: true });
        }
      },
      fail: () => {
        console.log("[guide:openDoor] fail", {
          options: this.data.pageOptions,
          packageId,
          taskId: this.data.taskId,
          boxNo,
          url: requestUrl
        });
        wx.showToast({ title: "网络错误", icon: "none" });
        this.openDeliveryError("network_error", "网络异常，未能完成开门请求。");
      },
      complete: () => {
        this.setData({ loadingAction: false });
      }
    });
  },

  confirmAndReturn() {
    const pages = getCurrentPages();
    const prevPage = pages[pages.length - 2];
    if (prevPage && prevPage.setData) {
      prevPage.setData({ needRefresh: true });
    }
    wx.navigateBack({ delta: 1 });
  },

  goToDeliveryError() {
    const message = this.data.role === "courier" ? "需要人工处理当前投递问题。" : "需要人工处理当前取件问题。";
    this.openDeliveryError("manual", message);
  },

  goVerifyInfo() {
    const source = this.data.role === "courier" ? (this.data.task || {}) : (this.data.pkg || {});
    const token = source.verifyToken || "";
    if (!token) {
      wx.showToast({ title: "暂无核验信息", icon: "none" });
      return;
    }
    wx.navigateTo({
      url:
        `/pages/order-verify/order-verify?token=${encodeURIComponent(token)}` +
        `&from=${encodeURIComponent(this.data.role || "user")}`
    });
  },

  scanVerifyQr() {
    scanAndOpenVerify(this.data.role === "courier" ? "courier" : "user");
  },

  goPackageDetail() {
    const pkg = this.data.pkg || {};
    if (!pkg.packageId) return;
    wx.navigateTo({
      url:
        `/pages/take-package/take-package?packageId=${encodeURIComponent(pkg.packageId)}` +
        `&role=${encodeURIComponent(this.data.role || "user")}`
    });
  }
});
