# JobRadar 岗位雷达

> 画像驱动的智能求职筛选系统——只采集、只评分、只排序，**投递永远由人决定**。

基于开源项目 [BossHunter v2.3.1](https://github.com/powerycy/BossHunter)（升级打怪开源社区）进行个人自用改造。感谢原作者 [@powerycy](https://github.com/powerycy) 的开源贡献。

## 定位

求职期间的本地岗位雷达：自动采集 BOSS直聘 岗位数据 → 按个人目标画像（可编辑的维度+权重+否决词）AI 评分排序 → 人工在岗位池里决定投哪个。

**核心原则：系统不代替你做任何对外动作。**

```
岗位采集 → 画像匹配评分 → 岗位池排序 → 人工跳转平台投递
                ↘ HR 回复监测 + 飞书提醒（只提醒，不代回）
```

## 与上游 BossHunter 的差异

| 改造点 | 说明 |
|---|---|
| 摘除自动投递 | 删除 deliver 执行器与全部投递路由，全流程 = 采集 + 评分即止 |
| 摘除自动回复/跟进 | 监测只做"HR 有新消息 → 飞书提醒"，不自动回复、不自动发简历 |
| 画像驱动评分（开发中） | 评分标准从"简历 vs JD"改为"目标岗位画像 vs JD"，画像可在前端编辑 |
| 手动标记全平台 | 岗位池支持手动标记"已发送"，覆盖全部平台 |
| 飞书通知 | 自建应用私聊（主）+ 群机器人 webhook（兜底）；cookie 失效自动告警 |
| Microsoft Edge 支持 | 浏览器预检放行 Edge（Chromium/CDP 兼容），使用独立采集 profile |

完整改造历史见项目 `docs/history.md`。

## 快速启动

前置：Windows + Python 3.13 venv（`.venv`）+ Edge 浏览器。

```powershell
# 双击或执行：
.\启动工作台.bat    # 起面板(127.0.0.1:8686) + 独立采集 Edge(CDP 9222)
.\停止工作台.bat    # 停面板 + 停采集 Edge（日常浏览器不受影响）
```

首次使用需在配置页填写：AI API Key、飞书通知参数（可选）、搜索关键词。

## 致谢与许可证

- 本项目源自 [BossHunter](https://github.com/powerycy/BossHunter) v2.3.1（基线 commit `d258ded`），上游原始 README 见 [docs/upstream-README.md](docs/upstream-README.md)
- **仅供个人自用**。BossHunter 上游许可证为非商业性质；若需商用，必须剥离其代码基础重写
- 上游自动化操作第三方招聘平台可能违反平台用户协议，风险自担
