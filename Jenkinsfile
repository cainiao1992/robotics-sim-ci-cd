// ===========================================================================
// Jenkins 声明式流水线：ROS 2 + Gazebo / Isaac Sim 端到端仿真回归
// 适用：Jenkins 2.x + Docker / Pipeline 插件 + 带 nvidia 运行时的 GPU Agent
// 用法：新建 Multibranch Pipeline，指向本 Jenkinsfile
// ===========================================================================

pipeline {
  agent none
  options {
    timestamps()
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '20'))
    timeout(time: 90, unit: 'MINUTES')
  }

  environment {
    REGISTRY       = 'registry.example.com/robotics'
    GAZEBO_IMAGE   = "${REGISTRY}/sim-gazebo:ros2-humble"
    ISAAC_IMAGE    = "${REGISTRY}/sim-isaac:6.0.1"
    SCENARIO_DIR   = 'sim/scenarios'
    SEED           = '42'
    METRIC_TOL     = '0.05'
    // GitLab 数字项目 ID 或 URL-encoded 路径（JOB_NAME 形如 repo/branch，不能直接当项目标识）
    GITLAB_PROJECT_ID = '123'
    // Jenkins 凭据：gitlab-token（Secret text），docker-creds（Username/password）
  }

  stages {
    stage('Build ROS 2') {
      agent { label 'cpu' }
      steps {
        checkout scm
        sh '''
          docker run --rm -v "$PWD":/ws -w /ws ros:humble-ros-base bash -c "
            apt-get update && apt-get install -y python3-colcon-common-extensions >/dev/null &&
            source /opt/ros/humble/setup.bash &&
            colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release &&
            source install/setup.bash && ros2 pkg list
          "
        '''
        stash includes: 'install/**,build/**,log/**', name: 'ros_ws'
      }
    }

    stage('Build Images') {
      when { branch 'main' }
      agent { label 'cpu' }
      steps {
        withCredentials([usernamePassword(credentialsId: 'docker-creds',
                         usernameVariable: 'DUSER', passwordVariable: 'DPASS')]) {
          sh 'echo "$DPASS" | docker login -u "$DUSER" --password-stdin "$REGISTRY"'
          sh "docker build -f docker/Dockerfile.gazebo -t ${GAZEBO_IMAGE} ."
          sh "docker push ${GAZEBO_IMAGE}"
        }
      }
    }

    stage('Sim Tests') {
      parallel {
        stage('Gazebo Smoke (MR)') {
          when { changeRequest() }
          agent { label 'cpu' }
          steps { runSim('gazebo', env.GAZEBO_IMAGE, 'smoke|critical', '0', '1') }
        }
        stage('Gazebo Full') {
          when { branch 'main' }
          agent { label 'cpu' }
          // 4 分片并行
          steps {
            parallelShards(4, { i ->
              runSim('gazebo', env.GAZEBO_IMAGE, '', "${i}", '4')
            })
          }
        }
        stage('Isaac Heavy (GPU)') {
          when { branch 'release/*' }
          agent { label 'gpu' }   // nvidia 运行时节点
          steps { runSim('isaac', env.ISAAC_IMAGE, 'isaac', '0', '1') }
        }
      }
    }

    stage('Link & Gate') {
      agent { label 'cpu' }
      when { changeRequest() }
      steps {
        // MR 路径仅 Gazebo Smoke（单分片）产出 sim_out，取回其结果
        unstash 'simout-gazebo-0'
        withCredentials([string(credentialsId: 'gitlab-token', variable: 'GITLAB_TOKEN')]) {
          // 单引号块由 shell 展开 $GITLAB_TOKEN，避免凭据经 Groovy 序列化进步骤参数
          sh '''
            pip install --quiet requests
            python3 scripts/ci_tool_link.py --provider gitlab \
              --project "$GITLAB_PROJECT_ID" --mr-iid "$CHANGE_ID" \
              --metrics sim_out/metrics.json --junit sim_out/junit.xml \
              --token "$GITLAB_TOKEN"
          '''
        }
      }
    }
  }

  post {
    always {
      // agent none 下 post 无节点上下文，须先占用节点；并汇总各分片 stash（未运行的分片跳过）
      node('cpu') {
        script {
          for (tag in ['gazebo-0', 'gazebo-1', 'gazebo-2', 'gazebo-3', 'isaac-0']) {
            try { unstash "simout-${tag}" } catch (ignored) { }
          }
        }
        junit testResults: '**/sim_out/**/junit.xml', allowEmptyResults: true
        archiveArtifacts artifacts: 'sim_out/**', allowEmptyArchive: true
      }
    }
    failure {
      slackSend channel: '#robotics-ci',
                message: "仿真回归失败: ${env.JOB_NAME} #${env.BUILD_NUMBER} (<${env.BUILD_URL}|查看>)"
    }
    success {
      echo '仿真回归通过，已回写 MR 评论与门禁状态'
    }
  }
}

// ---------------------------------------------------------------------------
// 复用的仿真执行闭包：拉镜像、挂载工作区、跑编排脚本、产出 junit + metrics
// ---------------------------------------------------------------------------
def runSim(String sim, String image, String filter, String shardIdx, String shardCount) {
  unstash 'ros_ws'
  // GPU 参数按需注入：宿主机无 nvidia 运行时时 --gpus all 会直接报错，CPU 节点不能带
  def gpuArgs = (sim == 'isaac') ? '--gpus all' : ''
  // 分片各用独立输出目录，避免并行闭包共享 workspace 时互相覆盖报告
  def outDir = (shardCount as int) > 1 ? "sim_out/shard-${shardIdx}" : 'sim_out'
  docker.image(image).inside(gpuArgs) {
    sh """
      source install/setup.bash
      python3 scripts/run_sim_tests.py \
        --scenario-dir ${SCENARIO_DIR} \
        --seed ${SEED} --tolerance ${METRIC_TOL} \
        --filter '${filter}' \
        --shard-index ${shardIdx} --shard-count ${shardCount} \
        --out-dir ${outDir}
    """
  }
  // 产物带回主上下文，供 Link & Gate 与 post 汇总；名称含分片号避免并发 stash 冲突
  stash includes: 'sim_out/**', name: "simout-${sim}-${shardIdx}", allowEmpty: true
}

// 并行分片辅助
def parallelShards(int n, Closure make) {
  def m = [:]
  for (int i = 0; i < n; i++) {
    def idx = i
    m["shard-${i}"] = { -> make(idx) }
  }
  parallel m
}
