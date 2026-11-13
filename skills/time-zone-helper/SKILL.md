---
name: time-zone-helper
description: 用户要把一个城市的时间换算成另一个城市的时间，或询问两个城市的时差时使用。
---

# 跨城市时间换算

## 步骤

1. 对涉及的每个城市，分别调用 get_current_time 工具查询当前时间。
2. 把两个城市的 UTC 偏移相减，得到时差。
3. 如果需要核对某个城市的时区或夏令时规则，读取 /skills/time-zone-helper/references/cities.md。
4. 用一句话给出结果，格式：北京 HH:MM，对应纽约 HH:MM（时差 N 小时）。
