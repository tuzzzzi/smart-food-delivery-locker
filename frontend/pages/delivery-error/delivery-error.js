const { getBaseUrl } = require("../../utils/request");
const BASE_URL = getBaseUrl();

function getToken() {
  return wx.getStorageSync("token") || wx.getStorageSync("openid") || "";
}

function authHeader() {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function decodeMessage(value) {
  if (!value) return "";
  try {
    return decodeURIComponent(value);
  } catch (error) {
    return value;
  }
}

function maskPhone(phone) {
  const value = String(phone || "").trim();
  if (!value) return "";
  if (value.length < 7) return value;
  return `${value.slice(0, 3)}****${value.slice(-4)}`;
}

function taskStatusText(status) {
  const map = {
    assigned: "待投递",
    reassigned: "待投递",
    waiting_ai: "AI检测中",
    ai_failed: "检测未通过",
    ai_passed: "检测通过",
    delivered: "已完成",
    completed: "已完成",
    pending: "异常处理中",
    resolved: "已处理完成",
    cancelled: "已关闭"
  };
  return map[status] || status || "状态同步中";
}

function packageStatusText(status) {
  if (status === "pending") return "待取件";
  if (status === "picked" || status === "completed") return "已完成";
  return status || "状态同步中";
}

function buildPageMeta(role, scene) {
  if (role === "courier") {
    const map = {
      ai_failed: {
        title: "投递检测未通过",
        desc: "系统没有确认包裹已正确入柜，请先确认现场情况，再选择重新投递或提交管理员处理。",
        sceneLabel: "检测异常",
        sceneClass: "danger",
        tips: ["确认包裹是否放入正确柜格。", "调整包裹摆放后可重新投递。", "多次失败时提交管理员处理。"]
      },
      ai_timeout: {
        title: "AI检测等待超时",
        desc: "系统暂未在预期时间内返回检测结果，需要人工判断下一步。",
        sceneLabel: "检测超时",
        sceneClass: "warning",
        tips: ["先返回任务刷新状态。", "现场仍可继续时重新投递。", "无法继续时提交管理员处理。"]
      },
      courier_hardware_timeout: {
        title: "柜门状态确认超时",
        desc: "开柜请求已发出，但柜门闭环状态没有及时同步。",
        sceneLabel: "柜门超时",
        sceneClass: "warning",
        tips: ["确认柜门是否已经打开并重新关闭。", "刷新任务状态后再决定是否重试。", "多次超时建议提交管理员处理。"]
      },
      courier_locker_exception: {
        title: "柜门状态异常",
        desc: "当前柜门状态需要现场确认，建议先停止重复操作。",
        sceneLabel: "柜门异常",
        sceneClass: "danger",
        tips: ["确认柜号和柜门是否匹配。", "能继续时返回任务重新投递。", "不能继续时提交管理员处理。"]
      },
      network_error: {
        title: "网络连接异常",
        desc: "请求没有成功返回，请先刷新任务状态，避免重复投递。",
        sceneLabel: "网络异常",
        sceneClass: "warning",
        tips: ["回到任务页刷新状态。", "确认包裹是否已入柜。", "状态不明确时提交管理员处理。"]
      },
      manual: {
        title: "投递异常处理建议",
        desc: "根据当前任务状态选择重新投递、返回任务或提交管理员处理。",
        sceneLabel: "人工处理",
        sceneClass: "neutral",
        tips: ["先确认柜号、包裹和柜门状态。", "现场可继续时重新投递。", "现场不可继续时提交管理员处理。"]
      }
    };
    return map[scene] || map.manual;
  }

  const userMap = {
    open_door_failed: {
      title: "开柜失败处理建议",
      desc: "本次开柜没有成功，请先确认包裹仍处于待取状态。",
      sceneLabel: "开柜失败",
      sceneClass: "danger",
      tips: ["确认自己在正确柜体前。", "刷新包裹状态后再重试。", "仍无法取件时查看管理员提醒。"]
    },
    hardware_timeout: {
      title: "柜门响应超时",
      desc: "开柜指令可能已经发出，但柜门状态还没有完成同步。",
      sceneLabel: "响应超时",
      sceneClass: "warning",
      tips: ["先确认柜门是否已经打开。", "包裹仍未取出时可返回取件。", "状态不确定时查看提醒并等待处理。"]
    },
    pickup_hardware_exception: {
      title: "取件柜门异常",
      desc: "系统检测到柜门状态异常，请避免连续重复开柜。",
      sceneLabel: "柜门异常",
      sceneClass: "danger",
      tips: ["确认柜门是否已经打开或关闭。", "包裹未取到时刷新后再试。", "多次失败时查看管理员提醒。"]
    },
    network_error: {
      title: "取件网络异常",
      desc: "请求没有成功返回，请刷新包裹状态后再继续。",
      sceneLabel: "网络异常",
      sceneClass: "warning",
      tips: ["先刷新包裹状态。", "仍为待取时返回取件。", "状态已变化时返回首页查看记录。"]
    },
    manual: {
      title: "取件问题处理建议",
      desc: "请先确认包裹状态，再选择返回取件、刷新状态或查看管理员提醒。",
      sceneLabel: "取件问题",
      sceneClass: "neutral",
      tips: ["确认柜号和包裹状态。", "包裹仍待取时可返回取件。", "多次失败时查看提醒并等待管理员处理。"]
    }
  };
  return userMap[scene] || userMap.manual;
}

function taskExceptionText(task) {
  if (!task) return "";
  if (task.exceptionStatusLabel) return task.exceptionStatusLabel;
  if (task.hasActiveException) return "异常处理中";
  if (task.exceptionResolved) return "已处理完成";
  return "";
}

function resolvedTaskMeta(task) {
  if (!task || !task.exceptionResolved) return null;
  return {
    pageTitle: "异常已处理完成",
    pageDesc: "当前异常已经完成业务处理，可返回任务查看最新状态。",
    sceneLabel: "已处理完成",
    sceneClass: "success",
    message: "",
    hintText: task.exceptionNote || "异常已处理完成。"
  };
}

Page({
  data: {
    role: "",
    scene: "manual",
    message: "",
    taskId: "",
    packageId: "",
    task: null,
    pkg: null,
    pageTitle: "异常处理",
    pageDesc: "",
    sceneLabel: "",
    sceneClass: "neutral",
    tips: [],
    hintText: "",
    actionLoading: ""
  },

  onLoad(options) {
    const role = options.role || "";
    const scene = options.scene || "manual";
    const meta = buildPageMeta(role, scene);

    this.setData({
      role,
      scene,
      message: decodeMessage(options.message || ""),
      taskId: options.taskId || "",
      packageId: options.packageId || "",
      pageTitle: meta.title,
      pageDesc: meta.desc,
      sceneLabel: meta.sceneLabel,
      sceneClass: meta.sceneClass,
      tips: meta.tips,
      hintText: role ? "" : "缺少异常上下文，请返回上一页重新进入。"
    });

    this.loadContext();
  },

  onShow() {
    this.loadContext();
  },

  onPullDownRefresh() {
    if (this.data.role === "courier") {
      this.fetchTask(() => wx.stopPullDownRefresh());
      return;
    }
    if (this.data.role === "user") {
      this.fetchPackage(() => wx.stopPullDownRefresh());
      return;
    }
    wx.stopPullDownRefresh();
  },

  loadContext() {
    if (this.data.role === "courier" && this.data.taskId) {
      this.fetchTask();
      return;
    }
    if (this.data.role === "user" && this.data.packageId) {
      this.fetchPackage();
    }
  },

  fetchTask(done) {
    const token = getToken();
    if (!token) {
      this.setData({ hintText: "登录状态已失效，请重新登录后再处理异常。" });
      done && done();
      return;
    }

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
          this.setData({
            task: null,
            hintText: data.message || "获取任务失败，请返回任务列表刷新。"
          });
          done && done();
          return;
        }

        const task = {
          ...data.task,
          statusText: taskStatusText(data.task.status),
          exceptionStatusText: taskExceptionText(data.task),
          receiverPhoneText: maskPhone(data.task.receiverPhone)
        };

        this.setData({ task }, () => {
          const resolvedMeta = resolvedTaskMeta(this.data.task);
          this.setData(resolvedMeta || { hintText: task.hasActiveException ? (task.exceptionNote || "") : "" });
        });
        done && done();
      },
      fail: () => {
        this.setData({ hintText: "网络异常，暂时无法获取任务详情。" });
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
          this.setData({
            pkg: null,
            hintText: data.message || "获取包裹状态失败，请返回首页刷新。"
          });
          done && done();
          return;
        }

        this.setData({
          pkg: {
            ...data.package,
            statusText: packageStatusText(data.package.status)
          },
          hintText: data.package.status === "pending" ? "" : "包裹状态已变化，请返回首页查看最新结果。"
        });
        done && done();
      },
      fail: () => {
        this.setData({ hintText: "网络异常，暂时无法获取包裹状态。" });
        done && done();
      }
    });
  },

  withActionLoading(key, fn) {
    if (this.data.actionLoading) return;
    this.setData({ actionLoading: key });
    fn(() => {
      this.setData({ actionLoading: "" });
    });
  },

  prepareRetry(nextStep) {
    const task = this.data.task || {};
    const taskId = this.data.taskId;
    if (!taskId) return;

    const jump = () => {
      if (nextStep === "guide") {
        wx.redirectTo({
          url: `/pages/guide/guide?role=courier&taskId=${encodeURIComponent(taskId)}`
        });
        return;
      }
      wx.showToast({ title: "已返回待处理", icon: "success" });
      setTimeout(() => {
        wx.reLaunch({ url: "/pages/courier-home/courier-home" });
      }, 250);
    };

    if (task.exceptionResolved || task.status === "assigned" || task.status === "reassigned") {
      jump();
      return;
    }

    this.withActionLoading(nextStep === "guide" ? "retry" : "pending", (done) => {
      wx.request({
        url: `${BASE_URL}/api/courier/reset_task_status`,
        method: "POST",
        header: {
          "content-type": "application/json",
          ...authHeader()
        },
        data: { taskId },
        success: (res) => {
          const data = res.data || {};
          if (data.status !== "success") {
            wx.showToast({ title: data.message || "处理失败", icon: "none" });
            this.fetchTask();
            done();
            return;
          }
          jump();
          done();
        },
        fail: () => {
          wx.showToast({ title: "网络异常", icon: "none" });
          done();
        }
      });
    });
  },

  retryTaskNow() {
    this.prepareRetry("guide");
  },

  returnTaskToPending() {
    this.prepareRetry("home");
  },

  cancelCurrentTask() {
    if (!this.data.taskId) return;

    this.withActionLoading("cancel", (done) => {
      wx.request({
        url: `${BASE_URL}/api/courier/cancel_task`,
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
            this.fetchTask();
            done();
            return;
          }

          wx.showToast({ title: "已提交管理员", icon: "success" });
          setTimeout(() => {
            wx.reLaunch({ url: "/pages/courier-home/courier-home" });
          }, 250);
          done();
        },
        fail: () => {
          wx.showToast({ title: "网络异常", icon: "none" });
          done();
        }
      });
    });
  },

  goCourierHome() {
    wx.reLaunch({ url: "/pages/courier-home/courier-home" });
  },

  retryOpenDoor() {
    const token = getToken();
    const pkg = this.data.pkg;
    if (!token) {
      wx.reLaunch({ url: "/pages/login/login" });
      return;
    }
    if (!pkg || pkg.status !== "pending") {
      wx.showToast({ title: "当前包裹状态不可重试", icon: "none" });
      this.fetchPackage();
      return;
    }

    this.withActionLoading("openDoor", (done) => {
      wx.request({
        url: `${BASE_URL}/api/user/open_door_by_package`,
        method: "POST",
        header: {
          "content-type": "application/json",
          ...authHeader()
        },
        data: {
          packageId: pkg.packageId
        },
        success: (res) => {
          const data = res.data || {};
          if (data.pendingHardware) {
            wx.showToast({ title: "开柜请求已发送", icon: "none" });
            wx.redirectTo({
              url: `/pages/guide/guide?role=user&packageId=${encodeURIComponent(pkg.packageId)}`
            });
            done();
            return;
          }
          if (data.status !== "success") {
            this.setData({
              message: data.message || "开柜失败，请稍后重试。",
              hintText: "包裹仍在待取状态时，可以继续重试或返回首页查看提醒。"
            });
            wx.showToast({ title: data.message || "开柜失败", icon: "none" });
            this.fetchPackage();
            done();
            return;
          }

          wx.showToast({ title: "取件完成", icon: "success" });
          setTimeout(() => {
            wx.reLaunch({ url: "/pages/user-home/user-home" });
          }, 250);
          done();
        },
        fail: () => {
          wx.showToast({ title: "网络异常", icon: "none" });
          done();
        }
      });
    });
  },

  refreshPackageStatus() {
    this.withActionLoading("refresh", (done) => {
      this.fetchPackage(() => {
        wx.showToast({ title: "状态已刷新", icon: "success" });
        done();
      });
    });
  },

  goRemindList() {
    wx.navigateTo({ url: "/pages/remind-list/remind-list" });
  },

  goUserHome() {
    wx.reLaunch({ url: "/pages/user-home/user-home" });
  }
});
