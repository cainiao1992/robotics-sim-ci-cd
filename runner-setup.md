# Runner / Agent GPU 配置指南

Isaac Sim 必须用 GPU（`--gpus all` + NVIDIA 运行时）；Gazebo 无头可在 CPU 跑。
按以下要点把执行节点准备好，流水线才能跑通。

---

## A. GitLab Runner（推荐，因 GitLab 原生 reports 支持最好）

### 1. 安装 runner
```bash
curl -L https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh | sudo bash
sudo apt-get install gitlab-runner
sudo gitlab-runner register   # 填入 GitLab URL / token，executor 选 docker
```

### 2. 开启 nvidia 运行时（Isaac Sim 节点）
宿主机装好 NVIDIA 驱动与 `nvidia-container-toolkit`：
```bash
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```
`config.toml` 中对应 runner 增加：
```toml
[[runners]]
  executor = "docker"
  [runners.docker]
    privileged = true
    runtime = "nvidia"          # 关键：启用 nvidia 运行时
    gpus = "all"
```
- 给 GPU 节点打 tag：`tags = ["gpu"]`，CPU 节点打 `["cpu"]`。
- 流水线用 `tags: [gpu]` 约束 Isaac 场景，避免抢占 CPU 节点。

### 3. 镜像缓存
Isaac Sim 镜像 >10GB，务必在节点预拉取并配置 registry 缓存，否则每次拉取拖垮流水线。

---

## B. Jenkins Agent

### 1. GPU 节点
- 节点标签：`gpu` / `cpu`。
- 安装 Docker + nvidia-container-toolkit，Jenkins 用「Docker Agent」模板或 `docker.image().inside("--gpus all")`。
- 凭据：`docker-creds`（Username/password）、`gitlab-token`（Secret text）。

### 2. 必需插件
Pipeline、Docker、JUnit、Slack Notification、GitLab Branch Source（多分支）。

---

## C. 通用检查清单

- [ ] `docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi` 能出显卡
- [ ] `docker run --rm sim/gazebo:ros2-humble gz sim --help` 正常
- [ ] `python3 scripts/run_sim_tests.py --self-test` 产出 junit.xml + metrics.json
- [ ] CI 变量已配置：`GITLAB_TOKEN`、`REGISTRY` 凭据、镜像地址
- [ ] 门禁阈值（tolerance）已根据一周基线合理设定
