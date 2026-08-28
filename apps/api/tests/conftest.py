"""测试环境统一离线：不读真实 LLM key，保证确定性与速度（CI 同理）。

真实 API 的端到端验证由 eval/run_eval.py 负责。
注意：环境变量优先级高于仓库根 .env，置空即可屏蔽。
"""

import os

os.environ["LLM_API_KEY"] = ""
