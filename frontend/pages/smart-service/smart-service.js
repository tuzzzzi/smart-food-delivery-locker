const FALLBACK_ANSWER = "暂未找到准确答案，可联系管理员或在异常页面提交问题。";

const QUICK_QUESTIONS = [
  { text: "如何取件" },
  { text: "如何使用取件码查件" },
  { text: "没有看到待取包裹怎么办" },
  { text: "柜门打不开怎么办" },
  { text: "取件后状态没更新怎么办" },
  { text: "配送员如何投递" },
  { text: "AI 检测失败怎么办" },
  { text: "如何联系管理员" }
];

const RULES = [
  {
    keywords: ["取件码", "查件", "六位", "6位", "查询"],
    answer: "在用户首页点击“取件码查件”，输入 6 位取件码后即可查找包裹。查询成功后可以查看柜号、入柜时间和入柜照片，并继续开始取件。"
  },
  {
    keywords: ["如何取件", "怎么取件", "取餐", "取外卖", "打开柜门"],
    answer: "进入用户首页后查看当前待取包裹，确认柜号后点击“立即取件”。在取件流程页点击“打开柜门取件”，取出包裹后请关闭柜门，系统会自动确认取件状态。"
  },
  {
    keywords: ["没有看到", "看不到", "没有待取", "待取包裹", "匹配不到"],
    answer: "请先确认个人资料中的手机号和订单收件手机号一致。也可以在首页使用“取件码查件”输入 6 位取件码查找。如果仍找不到，建议联系管理员核对订单信息。"
  },
  {
    keywords: ["柜门打不开", "打不开", "开不了", "开门失败", "门打不开"],
    answer: "请确认你所在柜号是否正确，并稍等几秒后重试。如果仍无法打开，请在取件流程页点击“取件遇到问题”提交管理员处理。"
  },
  {
    keywords: ["状态没更新", "没更新", "取件后", "已取出", "关门后", "状态不变"],
    answer: "取件后请确认柜门已经关闭。系统通常会在关门后自动确认状态，如果页面暂未更新，可以下拉刷新或稍后再看；仍异常时请提交管理员处理。"
  },
  {
    keywords: ["配送员如何投递", "如何投递", "配送员投递", "投递任务", "入柜"],
    answer: "配送员进入工作台后选择待投递任务，确认柜号和收件信息，点击开始投递并按页面引导开柜、放入包裹、关闭柜门，随后等待系统确认包裹入柜。"
  },
  {
    keywords: ["AI", "检测失败", "检测未通过", "包裹信息待确认", "重新检测"],
    answer: "如果系统暂未确认包裹，请先检查包裹是否放稳、是否被遮挡，再按页面提示继续等待或重新检测。多次未通过时，可以提交管理员处理。"
  },
  {
    keywords: ["联系管理员", "管理员", "人工", "客服", "异常", "问题"],
    answer: "遇到无法自行处理的问题，可以在取件流程页或配送异常页面提交问题给管理员，也可以请现场工作人员协助核对柜号和订单信息。"
  }
];

function normalize(text) {
  return String(text || "").toLowerCase().replace(/\s+/g, "");
}

function createMessage(from, text) {
  return {
    id: `msg-${Date.now()}-${Math.floor(Math.random() * 10000)}`,
    from,
    text
  };
}

function findAnswer(question) {
  const value = normalize(question);
  const matched = RULES.find((rule) => rule.keywords.some((keyword) => value.includes(normalize(keyword))));
  return matched ? matched.answer : FALLBACK_ANSWER;
}

Page({
  data: {
    inputText: "",
    quickQuestions: QUICK_QUESTIONS,
    messages: [
      createMessage("bot", "你好，我是智能客服。你可以问我取件、取件码查件、柜门问题、配送投递或管理员处理相关问题。")
    ],
    scrollIntoView: ""
  },

  onInput(e) {
    this.setData({ inputText: e.detail.value });
  },

  askQuick(e) {
    const question = e.currentTarget.dataset.question || "";
    this.replyToQuestion(question);
  },

  sendMessage() {
    const question = (this.data.inputText || "").trim();
    if (!question) {
      wx.showToast({ title: "请输入问题", icon: "none" });
      return;
    }
    this.replyToQuestion(question);
  },

  replyToQuestion(question) {
    const userMessage = createMessage("user", question);
    const botMessage = createMessage("bot", findAnswer(question));
    const messages = [...this.data.messages, userMessage, botMessage];
    this.setData({
      messages,
      inputText: "",
      scrollIntoView: botMessage.id
    });
  }
});
