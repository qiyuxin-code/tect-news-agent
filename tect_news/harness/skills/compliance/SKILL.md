# Skill: compliance（采集合规与事实约束）

- 只使用 collect_news 返回的语料与 URL；每条 source_url 必须逐字一致，禁止编造链接。
- 无法核实的传闻、未指明出处的数字，不写进 claim 或 context。
- context 只写语料中可核实的机制/算法/架构/工程手段，禁止用 star 数、融资额、排名等人气指标代替技术说明。
- 综述禁止出现 http 或裸 URL。
- 采集遵循 robots/TOS，抓取有上限，不因重试加剧目标站负担。
