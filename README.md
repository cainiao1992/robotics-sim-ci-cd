# CI/CD 工具链 × 仿真平台集成方案（GitLab / Jenkins × Isaac Sim / Gazebo）

> 目标：把机器人/自动驾驶等仿真验证嵌入 CI/CD，实现「代码提交 → 构建 → 无头仿真回归 → 指标采集 → 门禁 → 反馈联动」的端到端自动化。
>
> 适用栈（默认假设，可按需替换）：ROS 2（Humble/Iron）、Python 3.10+、Docker、GitLab CI 或 Jenkins；仿真器 Gazebo（Harmonic LTS）与 NVIDIA Isaac Sim 6.x。

---

## 1. 整体架构

端到端流水线分五层，每一层都是可被 CI 编排的独立阶段：

| 层 | 职责 | 关键产物 |
|----|------|----------|
| 1. 触发层 | MR/推送/标签/Schedule 触发；合并请求门禁 | Pipeline、Webhook 事件 |
| 2. 构建层 | 编译 ROS 2 包、构建仿真镜像、缓存依赖 | Docker 镜像、deb/wheel |
| 3. 仿真测试层 | 无头运行 Gazebo / Isaac Sim 场景，执行单元测试/集成测试/场景回归 | 仿真日志、rosbag、截图/视频 |
| 4. 结果采集层 | 解析指标、生成 JUnit XML、覆盖率、趋势数据 | `junit.xml`、`metrics.json`、artifacts |
| 5. 联动与门禁层 | 回写 MR 评论/状态、通知 IM、更新看板、决定是否放行 | MR 评论、徽章、门禁状态 |

工具联动的核心是「**事件总线 + API 回写**」：CI 作为编排中枢，仿真平台作为可执行能力，GitLab/Jenkins 的 API 与 Webhook 负责把结果推回代码评审与协作系统。

---

## 2. 关键设计决策

### 2.1 无头运行（必须解决）
- **Gazebo**：天然支持无渲染服务器模式，`gz sim -s -r scenario.sdf` 仅跑物理与传感器，CPU 即可；配合 `gz topic` / ROS 2 桥接采集数据。
- **Isaac Sim**：基于 Omniverse，渲染需 GPU。CI 中必须用 **带 NVIDIA 运行时的容器**（`nvcr.io/nvidia/isaac-sim:6.0.1`），以 `--headless --no-window` 运行 Python 脚本；自定义场景脚本通过 `isaacsim` 扩展 API 驱动。无 GPU 环境退化为「离线轨迹回放 + 物理校验」。

### 2.2 确定性
- 固定仿真步长与随机种子（`--seed 42`），关闭实时因子，保证同输入同输出，才能做回归对比。
- 用「基线快照」机制：首次通过的指标写入 baseline，后续构建与之对比，超阈值即失败。

### 2.3 资源与耗时
- 把仿真拆成可并行的场景矩阵（matrix / parallel stage），按场景分片缩短反馈时间。
- 重型 Isaac Sim 场景只在 nightly / release 分支跑，MR 分支跑轻量 Gazebo 冒烟 + 关键场景。

### 2.4 产物与存储
- 标准产物：JUnit XML（测试）、`metrics.json`（指标）、rosbag（调试）、MP4/截图（可视证据）。
- 大体积仿真产物用对象存储（MinIO/S3）或 GitLab Generic Packages，避免污染代码仓库。

---

## 3. 工具联动模式（重点）

| 联动方向 | 机制 | 示例 |
|----------|------|------|
| GitLab → Jenkins | GitLab Webhook 触发 Jenkins 多分支流水线 | 仿真重负载放在 Jenkins GPU 节点，轻量 CI 在 GitLab Runner |
| CI → GitLab MR | Pipeline API 写 Check 状态 + 评论脚本回贴指标 | `scripts/ci_tool_link.py --comment metrics` |
| CI → 看板 | 推送 `metrics.json` 到 Grafana Loki/InfluxDB | 仿真通过率趋势图 |
| CI → IM | 失败通知发 Slack/飞书/企业微信 | `post` 阶段 `failure` 分支 |
| Jira ← GitLab | 提交信息含 `JIRA-123`，CI 自动关联并回填仿真报告链接 | 需求-验证闭环 |

---

## 4. 文件清单（本仓库交付物）

```
sim-ci-cd-integration/
├── README.md                      # 本文档
├── gitlab-ci.yml                  # GitLab CI 流水线模板
├── Jenkinsfile                    # Jenkins 声明式流水线模板
├── runner-setup.md                # GitLab Runner / Jenkins Agent GPU 配置
├── docker/
│   ├── Dockerfile.gazebo          # 无头 Gazebo + ROS 2 镜像
│   └── Dockerfile.isaac           # Isaac Sim 基础 + 依赖镜像
├── docker-compose.sim.yml         # 本地一键跑仿真回归
├── requirements-isaac.txt         # Isaac 镜像的 Python 依赖清单
└── scripts/
    ├── run_sim_tests.py           # 统一仿真编排，产出 JUnit XML + metrics.json
    └── ci_tool_link.py            # 与 GitLab/Jenkins API 回写联动
```

---

## 5. 落地步骤

1. **准备 Runner/Agent**：按 `runner-setup.md` 配置带 `nvidia` 运行时的 GitLab Runner / Jenkins Agent（Isaac Sim 必需）。
2. **构建镜像**：`docker build -f docker/Dockerfile.gazebo -t sim/gazebo:ros2 .`
3. **接入流水线**：把 `gitlab-ci.yml` 或 `Jenkinsfile` 放到仓库根，按注释填变量（镜像地址、场景路径、GPU 标签）。
4. **联调**：本地用 `docker-compose.sim.yml` 先跑通 `scripts/run_sim_tests.py`，确认产出 `junit.xml` 与 `metrics.json`。
5. **开联动**：在 CI 变量里配置 `GITLAB_TOKEN` / Jenkins 凭据，启用 `ci_tool_link.py` 回写 MR 评论与门禁。
6. **调门禁阈值**：观察一周基线，设置合理的指标容差（如轨迹误差 < 5%）。

---

## 6. 常见坑

- Isaac Sim 容器体积大（>10GB），务必配镜像缓存与分层构建，否则每次拉取拖垮流水线。
- Gazebo 与 Isaac Sim 的 URDF/SDF 坐标系、物理引擎参数不同，跨平台对拍要做单位/坐标系归一化。
- ROS 2 Humble 官方配对的 Gazebo 是 Fortress；本模板选 Harmonic（LTS 至 2029-05），其桥接包 `ros-humble-ros-gzharmonic` 由 OSRF 提供（非官方 ROS 包），不可与 Fortress 系包（`ros-humble-ros-gz*` 通配安装）混装，镜像内已显式固定。
- 无头渲染缺少 GPU 时 Isaac Sim 会直接报错，需用 `docker run --gpus all` 或在 CI 标签上约束到 GPU 节点。
- 仿真偶发非确定性会导致 flaky test，务必先固定种子 + 关闭实时，再上线门禁。
