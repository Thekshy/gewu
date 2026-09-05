// k6 压测：/api/chat SSE 对话路径（零 key 确定性链路）。
//
// 口径（报告需注明）：
// - TTFT ≈ k6 http_req_waiting（首字节时间）——SSE 响应的首字节即首帧写出；
// - 目标链路：gateway → orchestrator → conversation → generate（全 RPC 链路，
//   零 key 无 LLM 外呼，度量的是微服务自身开销）；
// - 场景：默认 30 VU / 60s；RATE_LIMIT_PER_MINUTE 需调高（如 100000），
//   DAILY_TOKEN_BUDGET 不受影响（零 key 无计量）。
//
// 用法：k6 run -e BASE_URL=http://127.0.0.1:8000 eval/k6-chat.js

import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8000';
const VUS = Number(__ENV.VUS || 30);
const DURATION = __ENV.DURATION || '60s';

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    // 验收阈值：链路自身开销毫秒级——超阈值说明回归（不算基线数字）
    'http_req_waiting{scenario:default}': ['p(95)<500'],
    'http_req_failed{scenario:default}': ['rate<0.01'],
  },
};

const QUESTIONS = [
  '帮我预约明天晚上的羽毛球馆打班级比赛',
  '现在有哪些场馆可以预约',
  '帮我提交明天一天的病假申请',
  '帮我预约研讨间301',
];

export default function () {
  const sid = `k6-${__VU}-${__ITER}`;
  const q = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  const res = http.post(`${BASE_URL}/api/chat`,
    JSON.stringify({ question: q, session_id: sid, role: 'student' }),
    { headers: { 'Content-Type': 'application/json' }, timeout: '30s' });

  check(res, {
    'status 200': (r) => r.status === 200,
    'SSE 完成（含 done 事件）': (r) => r.body.includes('"type":"done"'),
    '无 error 事件': (r) => !r.body.includes('"type":"error"'),
  });
  sleep(0.2);
}
