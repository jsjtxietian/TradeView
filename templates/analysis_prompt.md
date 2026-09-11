# 交易分析任务

我的交易哲学继承 Livermore、Darvas、Mark Minervini 等趋势交易体系。我的偏好很明确：

- 保护本金：硬止损线 + 绝不向下平摊亏损
- 让市场证明自己是对的：不抄底接飞刀，只跟随趋势，行情好择时加仓 + 行情不好减仓休息
- 优先做行业/主题龙头股，小盘买突破，大盘买回调
- 不上杠杆、不做空、不玩妖股

请你先判断这个标的是个股还是 ETF，再决定分析路径。

- 如果是个股：按下面完整要求分析。
- 如果是 ETF：不要进行个股相关分析，应改为分析 ETF 的主题、主要成分股及其表现、催化因素与风险。

请你帮我判断这个标的现在是否适合进入观察名单、试仓或等待更好的位置；如果我有该标的的持仓，则分析加仓、退出的时机。

股票代码：{{symbol}}
数据截止：{{latest_date}}

我的当前标的持仓信息：

{{holding_block}}



#### 我掌握的技术面摘要

{{technical_summary}}

#### 我的补充笔记
{{note_block}}



#### 任务

请你自行搜索并补充：
1. 最近几个季度的基本面变化，包括营收、EPS、利润率、指引与市场预期；是否存在 Code 33 情况：连续三个季度 earnings、sales、profit margins 加速改善。
2. 不要把我提供的本地“相对 SPY 表现分”当成 IBD 官方 RS Rating。它只是用标的股价相对 SPY 的加权超额收益，衡量标的是不是比大盘强，真实的相对强度排名可以搜索找找看。
3. 它是否是所属行业/主题里的龙头股；所属行业/主题当前是否处在强化阶段，最近的重要新闻、财报、产品周期或政策催化。
4. 最近一次财报日、下一次预期财报日分别是什么，距离下一次财报还有多久。
5. 结合我提供的最近 K 线原始量价线索以及一些趋势原则和你自行搜索到的信息（包括该标的的机构持股比例等信息），判断机构资金更像在吸筹还是派发，并说明线索。
6. 也考虑一些形态，比如CAN SLIM体系，Mark Minervini 的 VCP等等。
7. 对大小盘股的策略可以稍微不一样：`The best time to buy the large-cap names is coming out of a bear market or a deep correction. With small caps, I tend to trade them close to new highs because they’re less efficiently priced, so I don’t have to “beat the crowd” and try to buy lower.`

最后请按下面结构输出：
1. 目前更像“可考虑买入 / 继续观察 / 暂不考虑”哪一种，如果有持仓，则建议如何操作。
2. 核心理由。
3. 若要介入，理想的触发条件、止损思路和失效信号是什么；如果临近财报，请明确说明是否应尽量避免赌财报。
4. 最大的不确定性或反例是什么。
