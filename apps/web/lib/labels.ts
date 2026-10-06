import type { DoneReason } from "./api";

/** route 事件取值（PARITY §3）→ 中文标签。 */
export const ROUTE_LABEL: Record<string, string> = {
  factual: "直答",
  research: "深度研究",
  refusal: "范围外",
  transaction: "办理",
  hybrid: "问答 + 办理",
  chitchat: "寒暄",
};

/** 用户角色（P21 服务端权威）→ 中文标签。guest=P39 免登游客影子用户。 */
export const ROLE_LABEL: Record<string, string> = {
  student: "学生",
  counselor: "辅导员",
  admin: "管理员",
  guest: "游客",
};

// transaction.go slotMetaTable 的槽位中文名（问题全文由 answer_delta 承载，标签只标注差哪个槽）
export const SLOT_LABEL: Record<string, string> = {
  venue: "场馆",
  date: "日期",
  slot: "时段",
  purpose: "用途",
  leave_type: "请假类型",
  start_date: "开始日期",
  end_date: "结束日期",
  reason: "事由",
  booking_id: "预约单号",
  ticket_id: "请假单号",
};

/** done.reason（P10 §4）四值的用户可见呈现；completed 无徽标。 */
export const DONE_BADGE: Record<DoneReason, { text: string; cls: string } | null> = {
  completed: null,
  max_tokens: { text: "已达长度上限，可能被截断", cls: "warn" },
  error: { text: "本轮出错", cls: "fail" },
  aborted: { text: "连接中断", cls: "warn" },
};
