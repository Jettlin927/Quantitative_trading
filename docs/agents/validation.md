# 变更验证

验证应与变更风险匹配：先运行最接近改动的检查，再扩展到受影响子系统。不得把未运行、
跳过或因环境缺失而中止的检查写成通过。

## 通用检查

```bash
git diff --check
```

纯文档修改通常只需检查差异、链接和相关文档合同；不要求无关的应用构建。

## 后端

修改过的 Python 文件先执行语法检查：

```bash
python -m py_compile <修改过的 Python 文件>
```

涉及后端行为时运行对应测试；影响范围较广时运行全量单元测试：

```bash
python -m unittest discover backend/tests -v
```

涉及 migration、数据质量、快照、runner、Worker 或 PostgreSQL 方言行为时还要运行：

```bash
scripts/ops/test_postgres_integration.sh
```

生产 migration 必须先在隔离 PostgreSQL 中验证；测试通过不构成生产执行授权。

## 前端

完整前端门禁运行：

```bash
scripts/ops/test_frontend_production_image.sh
```

开发阶段可先针对改动运行 `npm run typecheck`、`npm run lint` 或 `npm run build`，
但不得用局部检查替代要求的生产镜像门禁。视觉或交互修改还应在浏览器中验收关键
状态、错误状态、窄屏布局和只读边界。

## Compose 与脚本

修改 Compose、Dockerfile、依赖或启动方式时运行：

```bash
docker compose config
```

Shell 脚本至少运行 `bash -n <脚本>`；Windows `.cmd` 文件必须保留 UTF-8 code page
和脚本目录切换：

```bat
chcp 65001 >nul
cd /d "%~dp0"
```

## 环境限制

Docker、PostgreSQL、浏览器或外部服务不可用时，应明确记录未验证项目和原因，不得用
较弱检查冒充完整验收。生产状态、数据库事实和部署提交必须在目标环境现场读回。
