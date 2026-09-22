const { getBaseUrl } = require("../../utils/request");
const { scanAndOpenVerify } = require("../../utils/qr-verify");
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

function maskPhone(phone) {
  const value = String(phone || "").trim();
  if (!value) return "";
  if (value.length < 7) return value;
  return `${value.slice(0, 3)}****${value.slice(-4)}`;
}

function localProfileKey(openid) {
  return `courierProfileExtra:${openid || "default"}`;
}

function readLocalCourierProfile(openid) {
  return wx.getStorageSync(localProfileKey(openid)) || {};
}

function resolveCourierIdentityStatus() {
  const roles = wx.getStorageSync("roles") || [];
  const currentRole = wx.getStorageSync("currentRole") || "";
  if (currentRole === "courier") return "已认证配送员";
  if (Array.isArray(roles) && roles.includes("courier")) return "已认证配送员";
  if (typeof roles === "string" && roles.includes("courier")) return "已认证配送员";
  return "审核中";
}

function sanitizeCourierText(message, fallback = "") {
  const text = String(message || "").trim();
  if (!text) return fallback;
  if (/YOLO|YOLOv5|执行成功|未检测到包裹目标|未检测到目标|系统确认未通过|确认未通过|no object|no package/i.test(text)) {
    return "系统已完成图片检测，但暂未确认包裹入柜。";
  }
  return text;
}

function courierStatusTitle(task) {
  const status = task.status || "";
  if (status === "ai_failed") return "未确认包裹入柜";
  if (status === "waiting_ai") return "正在检测包裹";
  if (status === "assigned" || status === "reassigned") return "待投递";
  if (status === "ai_passed" || status === "delivered" || status === "completed") return "已完成";
  if (task.hasActiveException) return "异常待处理";
  return statusTextFromTask(task);
}

function resolveRecommendation(task, fallbackText) {
  if (task.status === "ai_failed") {
    return "请检查包裹摆放后重新投递。";
  }
  if (task.hasActiveException) {
    return sanitizeCourierText(task.exceptionNote || task.aiMessage, "请优先处理当前异常任务。");
  }
  return sanitizeCourierText(fallbackText, "请根据当前任务状态继续处理。");
}

function statusTextFromTask(task) {
  const map = {
    assigned: "待投递",
    reassigned: "待投递",
    waiting_ai: "AI检测中",
    ai_failed: "未确认入柜",
    ai_passed: "检测通过",
    delivered: "已完成",
    completed: "已完成",
    pending: "异常处理中",
    resolved: "已处理完成",
    cancelled: "已关闭"
  };
  return map[task.status] || task.status || "状态同步中";
}

function resolveNextStepText(task, stageKey) {
  if (task.hasActiveException) {
    return sanitizeCourierText(task.exceptionNote || task.aiMessage, "当前任务存在未处理异常，请优先处理。");
  }
  if (task.exceptionResolved) {
    return sanitizeCourierText(task.exceptionNote, "原异常已处理完成，当前任务已恢复到正常流程。");
  }
  if (task.status === "assigned" || task.status === "reassigned") {
    return `前往柜号 ${task.boxNo || "-"}，打开柜门、放入包裹并关闭柜门。`;
  }
  if (task.status === "waiting_ai") {
    return "包裹已提交检测，请等待 AI 检测结果。";
  }
  if (task.status === "ai_failed") {
    return "系统已完成图片检测，但暂未确认包裹入柜。";
  }
  if (task.status === "ai_passed" || task.status === "delivered" || task.status === "completed") {
    return "当前任务已完成，可以继续处理其他任务。";
  }
  if (stageKey === "closed" || task.status === "cancelled") {
    return "该任务已关闭，建议查看关闭原因并决定是否联系管理员。";
  }
  return "请根据当前任务状态继续处理。";
}

function resolveActionText(task, stageKey) {
  if (stageKey === "closed" || task.status === "cancelled") return "查看关闭原因";
  if (task.status === "assigned" || task.status === "reassigned") return "开始投递";
  if (task.status === "ai_failed") return "重新投递";
  if (task.status === "waiting_ai") return "查看检测";
  if (stageKey === "completed") return "查看结果";
  return "查看任务";
}

function resolveStageTone(task, stageKey) {
  if (task.hasActiveException) return "danger";
  if (task.status === "ai_failed") return "danger";
  if (stageKey === "pending") return "waiting";
  if (stageKey === "in_progress") return "busy";
  if (stageKey === "completed") return "done";
  if (stageKey === "closed") return "closed";
  return "waiting";
}

function normalizeTask(task, sectionKey) {
  const taskStage = task.taskStage || {};
  const platformOrderStatus = task.platformOrderStatus || {};
  const stageKey = taskStage.key || sectionKey || "";
  const isPlatformTask = task.source === "platform_order";
  const showErrorAction = !!task.hasActiveException || task.status === "ai_failed" || stageKey === "closed";
  const exceptionStatusLabel = task.exceptionStatusLabel || (task.hasActiveException ? "异常处理中" : (task.exceptionResolved ? "异常已处理完成" : ""));
  const rawSummaryText =
    task.exceptionNote ||
    task.aiMessage ||
    taskStage.description ||
    platformOrderStatus.description ||
    "";
  const nextStepText = resolveNextStepText(task, stageKey);
  const recommendationText = resolveRecommendation(task, nextStepText);

  return {
    ...task,
    displayTitle: (task.merchantName || "").trim() || (isPlatformTask ? "配送任务" : "临时投递"),
    sourceText: isPlatformTask ? "平台任务" : "临时任务",
    sourceClass: isPlatformTask ? "platform" : "manual",
    receiverPhoneText: maskPhone(task.receiverPhone) || "未填写手机号",
    statusText: statusTextFromTask(task),
    stageKey,
    stageLabel: taskStage.label || statusTextFromTask(task),
    stageDesc: taskStage.description || "",
    orderStatusLabel: platformOrderStatus.label || "",
    orderStatusDesc: platformOrderStatus.description || "",
    boxText: task.boxNo || "-",
    actionText: resolveActionText(task, stageKey),
    nextStepText,
    statusTitle: courierStatusTitle(task),
    recommendationText,
    problemText: task.status === "ai_failed"
      ? "系统已完成图片检测，但暂未确认包裹入柜。"
      : sanitizeCourierText(rawSummaryText),
    stageTone: resolveStageTone(task, stageKey),
    showErrorAction,
    exceptionStatusLabel,
    summaryText: sanitizeCourierText(rawSummaryText)
  };
}

function pickFocusTask(groups) {
  const urgent = groups.inProgress.find((item) => item.hasActiveException) || groups.inProgress.find((item) => item.status === "ai_failed");
  return urgent || groups.pending[0] || groups.inProgress[0] || groups.closed[0] || groups.completed[0] || null;
}

function withoutFocus(items, focusTask) {
  const focusId = focusTask && focusTask.taskId;
  if (!focusId) return items || [];
  return (items || []).filter((item) => item.taskId !== focusId);
}

function resolveWorkbenchStatus(groups) {
  const hasActiveException = groups.pending.some((item) => item.hasActiveException) || groups.inProgress.some((item) => item.hasActiveException);
  const hasAiFailed = groups.inProgress.some((item) => item.status === "ai_failed");

  if (hasActiveException) {
    return {
      text: "异常待处理",
      className: "danger",
      tip: "有任务仍存在未处理异常，请优先进入异常处理或重新投递。"
    };
  }

  if (hasAiFailed) {
    return {
      text: "异常待处理",
      className: "danger",
      tip: "有任务未通过系统确认，请优先处理当前入柜或进入异常处理。"
    };
  }

  if (groups.inProgress.length) {
    return {
      text: "执行中",
      className: "busy",
      tip: "你有任务正在入柜、等待确认或继续处理中。"
    };
  }

  if (groups.pending.length) {
    return {
      text: "待投递",
      className: "waiting",
      tip: "已有任务分配给你，下一步是前往对应柜号完成投递。"
    };
  }

  if (groups.closed.length) {
    return {
      text: "有关闭记录",
      className: "danger",
      tip: "最近存在已关闭或异常任务，可查看关闭原因并联系管理员继续承接。"
    };
  }

  if (groups.completed.length) {
    return {
      text: "已完成",
      className: "done",
      tip: "当前没有新任务，最近任务已完成入柜。"
    };
  }

  return {
    text: "空闲",
    className: "idle",
    tip: "当前没有待处理任务，可以等待新任务或新增临时投递。"
  };
}

Page({
  data: {
    courierOpenid: "",
    courierShort: "",
    userName: "",
    identityStatusText: "已认证配送员",
    serviceAreaText: "校园外卖柜",

    focusTask: null,
    pendingTasks: [],
    inProgressTasks: [],
    completedTasks: [],
    closedTasks: [],
    displayPendingTasks: [],
    displayInProgressTasks: [],
    displayCompletedTasks: [],
    visibleCompletedTasks: [],
    displayClosedTasks: [],
    hiddenCompletedCount: 0,
    showAllCompleted: false,

    pendingCount: 0,
    inProgressCount: 0,
    completedCount: 0,
    closedCount: 0,
    totalCount: 0,

    statusText: "空闲",
    statusClass: "idle",
    statusTip: "当前没有待处理任务。",

    loadingOverview: false,
    hintText: ""
  },

  onLoad() {
    const openid = wx.getStorageSync("openid") || "";
    if (!openid) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }

    const short = openid.length > 10 ? `${openid.slice(0, 10)}...` : openid;
    const cachedName = (wx.getStorageSync("username") || "").trim();

    this.setData({
      courierOpenid: openid,
      courierShort: short,
      userName: cachedName || short
    });
    this.refreshCourierIdentity();
  },

  onShow() {
    if (!this.data.courierOpenid) return;
    this.refreshCourierIdentity();
    this.refreshAll();
  },

  onPullDownRefresh() {
    this.refreshAll(true);
  },

  refreshAll(fromPullDown = false) {
    const isPullDown = fromPullDown === true;
    this.fetchProfileAndCache();
    this.fetchTasksOverview(isPullDown);
  },

  refreshCourierIdentity() {
    const extra = readLocalCourierProfile(this.data.courierOpenid);
    this.setData({
      identityStatusText: resolveCourierIdentityStatus(),
      serviceAreaText: (extra.serviceArea || "").trim() || "校园外卖柜"
    });
  },

  fetchProfileAndCache() {
    const openid = this.data.courierOpenid;
    if (!openid) return;

    wx.request({
      url: `${BASE_URL}/api/courier/profile`,
      method: "GET",
      header: authHeader(),
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") return;

        const courier = data.courier || {};
        const saved = readLocalCourierProfile(openid);
        const name = (
          saved.courierName ||
          courier.courierName ||
          courier.courier_name ||
          courier.nickname ||
          courier.username ||
          ""
        ).trim();
        if (!name) return;

        wx.setStorageSync("username", name);
        this.setData({ userName: name });
      }
    });
  },

  fetchTasksOverview(fromPullDown = false) {
    const token = getToken();
    if (!token || !this.data.courierOpenid) {
      this.setData({ hintText: "登录状态已失效，请重新登录。" });
      if (fromPullDown) wx.stopPullDownRefresh();
      return;
    }

    this.setData({ loadingOverview: true, hintText: "" });

    wx.request({
      url: `${BASE_URL}/api/courier/tasks_overview`,
      method: "GET",
      header: {
        "content-type": "application/json",
        "Authorization": `Bearer ${token}`
      },
      success: (res) => {
        const data = res.data || {};
        if (data.status !== "success") {
          this.setData({ hintText: data.message || "获取任务概览失败" });
          return;
        }

        const groups = {
          pending: (data.pending || []).map((task) => normalizeTask(task, "pending")),
          inProgress: (data.inProgress || []).map((task) => normalizeTask(task, "in_progress")),
          completed: (data.completed || []).map((task) => normalizeTask(task, "completed")),
          closed: (data.closed || []).map((task) => normalizeTask(task, "closed"))
        };

        const workbenchStatus = resolveWorkbenchStatus(groups);
        const focusTask = pickFocusTask(groups);
        const displayPendingTasks = withoutFocus(groups.pending, focusTask);
        const displayInProgressTasks = withoutFocus(groups.inProgress, focusTask);
        const displayCompletedTasks = withoutFocus(groups.completed, focusTask);
        const displayClosedTasks = withoutFocus(groups.closed, focusTask);
        const visibleCompletedTasks = this.data.showAllCompleted
          ? displayCompletedTasks
          : displayCompletedTasks.slice(0, 1);

        this.setData({
          focusTask,
          pendingTasks: groups.pending,
          inProgressTasks: groups.inProgress,
          completedTasks: groups.completed,
          closedTasks: groups.closed,
          displayPendingTasks,
          displayInProgressTasks,
          displayCompletedTasks,
          visibleCompletedTasks,
          displayClosedTasks,
          hiddenCompletedCount: Math.max(displayCompletedTasks.length - visibleCompletedTasks.length, 0),
          pendingCount: groups.pending.length,
          inProgressCount: groups.inProgress.length,
          completedCount: groups.completed.length,
          closedCount: groups.closed.length,
          totalCount: groups.pending.length + groups.inProgress.length + groups.completed.length + groups.closed.length,
          statusText: workbenchStatus.text,
          statusClass: workbenchStatus.className,
          statusTip: workbenchStatus.tip,
          hintText: ""
        });
      },
      fail: () => {
        this.setData({ hintText: "网络错误：无法获取任务概览。" });
      },
      complete: () => {
        this.setData({ loadingOverview: false });
        if (fromPullDown) wx.stopPullDownRefresh();
      }
    });
  },

  openTaskFlow(e) {
    const taskId = e.currentTarget.dataset.taskid || (this.data.focusTask && this.data.focusTask.taskId);
    if (!taskId) return;

    wx.navigateTo({
      url: `/pages/guide/guide?role=courier&taskId=${encodeURIComponent(taskId)}`
    });
  },

  openTaskError(e) {
    const taskId = e.currentTarget.dataset.taskid || (this.data.focusTask && this.data.focusTask.taskId) || "";
    const url =
      `/pages/delivery-error/delivery-error?role=courier&scene=manual&taskId=${encodeURIComponent(taskId)}` +
      "&message=" + encodeURIComponent("当前任务需要人工处理，请根据页面建议继续操作。");
    wx.navigateTo({ url });
  },

  goCreate() {
    wx.navigateTo({ url: "/pages/courier-create/courier-create" });
  },

  goCourierProfile() {
    wx.navigateTo({ url: "/pages/courier-profile/courier-profile" });
  },

  goSmartService() {
    wx.navigateTo({ url: "/pages/smart-service/smart-service?role=courier" });
  },

  scanVerifyQr() {
    scanAndOpenVerify("courier");
  },

  toggleCompleted() {
    const showAllCompleted = !this.data.showAllCompleted;
    const displayCompletedTasks = this.data.displayCompletedTasks || [];
    const visibleCompletedTasks = showAllCompleted
      ? displayCompletedTasks
      : displayCompletedTasks.slice(0, 1);
    this.setData({
      showAllCompleted,
      visibleCompletedTasks,
      hiddenCompletedCount: Math.max(displayCompletedTasks.length - visibleCompletedTasks.length, 0)
    });
  },

  goError() {
    const taskId = (this.data.focusTask && this.data.focusTask.taskId) || "";
    const url =
      `/pages/delivery-error/delivery-error?role=courier&scene=manual&taskId=${encodeURIComponent(taskId)}` +
      "&message=" + encodeURIComponent("需要人工处理当前投递异常。");
    wx.navigateTo({ url });
  },

  switchIdentity() {
    clearAuthStorage();
    wx.reLaunch({ url: "/pages/login/login" });
  }
});
