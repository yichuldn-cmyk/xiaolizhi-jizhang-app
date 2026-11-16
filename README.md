# 小荔枝 · 家庭记账

## 框架与边界（先确定架构，再实现）

采用 Python 3.10+ / Flask 应用工厂 + Jinja 服务端模板 + SQLite + 原生 CSS/JavaScript。生产运行使用 Waitress；无 Node 构建步骤，不使用 ORM，金额统一保存为整数分。按 web（路由与身份）、services（账单与统计）、importer（文件解析）、db（持久化）划分职责。SQL 使用参数绑定，模板默认转义，写操作使用 CSRF 防护。

本项目是响应式网页，不是微信原生小程序。微信和支付宝个人账单采用用户导出 CSV/XLSX 后导入，不能自动拉取个人全量支付流水。不保存支付密码。家庭成员使用独立账号，通过一次性限时邀请加入；私有账单仅本人可见。家庭共享统计包含显式共享的账单，家庭内部转账不计消费。

## 计划目录

```text
app/
  __init__.py          应用工厂、配置、通用安全与错误处理
  db.py               数据表、连接生命周期
  web.py              登录、页面、表单与导入流程
  services.py         权限、金额、统计、分类、预算与提醒
  importer.py         微信/支付宝/通用 CSV、XLSX 解析
  templates/          Jinja 模板
  static/             响应式样式、少量交互、家庭插画
docs/PARAMETERS.md    配置、表单、导入参数和统计口径
tests/               业务与权限集成测试（标准库 unittest）
examples/            可导入的示例账单
run.py               Waitress 启动入口
requirements.txt     锁定的直接依赖
data/                运行生成的数据库与本机密钥（不纳入版本管理）
```

## 已实现

- 与示例方向一致的绿色主题、家庭花园插画、环形图、手机底栏和桌面侧栏。
- 独立账号注册登录、家庭邀请（一次性/24小时）、退出家庭、逐笔和整批共享选择。
- 微信/支付宝常见CSV、XLSX账单字段适配：预览后确认、整数分计算、去重、错误行说明、整批撤销。
- 手动新增/编辑、收入/支出/转账/退款、内置和自定义分类、搜索筛选、CSV导出、回收站恢复。
- 家庭月总预算/分类预算、80%/100%站内预警、分类统计、每日净支出趋势、每日站内提醒。
- CSRF、密码哈希、SQL参数绑定、家庭数据权限隔离、登录失败限流、上传限制、SQLite一致性备份。

## 快速开始（Windows PowerShell）

**最简方式：双击 `打开账本.cmd` 使用正式账本，或双击 `打开演示.cmd` 查看演示。** 原英文入口 `start.cmd` / `start-demo.cmd` 同样可用。启动器会后台运行服务、等待就绪再打开浏览器，启动窗口关闭后服务继续运行，重复双击会复用已运行服务。电脑重启后再次双击即可。入口不依赖PowerShell脚本执行策略。

开发环境已在本目录 `.venv` 安装依赖。直接执行：

```powershell
Set-Location -LiteralPath 'C:\00 AI\chatgpt\【小荔枝】记账小程序'
.\.venv\Scripts\python.exe run.py
```

浏览器打开 `http://127.0.0.1:8000`，首次使用创建账号。正式账本为空，不预置演示账号。保持终端运行，Ctrl+C停止。也可运行 `start.ps1`。

### 只看演示

在新开的PowerShell窗口中运行：

```powershell
Set-Location -LiteralPath 'C:\00 AI\chatgpt\【小荔枝】记账小程序'
.\start-demo.ps1
```

打开 `http://127.0.0.1:8001`，账号 `demo` 或 `family`，密码均为 `demo-lizhi-2026`。演示数据在 `data/demo/`，与默认正式数据隔离。不要把演示账号用于真实家庭账单。脚本设置的环境变量仅作用于当前PowerShell，正式使用请新开窗口。

### 换电脑 / 重新安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

若所在网络访问官方PyPI缓慢，可使用可信镜像：`-i https://pypi.tuna.tsinghua.edu.cn/simple`。不要关闭TLS证书校验。本项目未使用外部CDN，安装完成后本地功能不依赖互联网。

## 手机访问

同一局域网内运行：

```powershell
$env:HOST='0.0.0.0'
.\.venv\Scripts\python.exe run.py
```

手机使用 `http://电脑的局域网IP:8000`。电脑需保持开机；如Windows询问网络访问权限，请仅按实际需要允许可信的专用网络。`127.0.0.1`是设备自己，不能发给家人作为远程链接。此版本未部署公网网址，离开局域网不能访问；部署需服务器、HTTPS反向代理、`COOKIE_SECURE=1`和受控数据目录，使用真实数据前应完成部署环境验收。不要把演示服务直接暴露到公网。

## 数据流程与规则

```text
浏览器表单 → Flask身份/CSRF → web路由 → services业务 / importer解析 → SQLite
SQLite查询 → Python汇总 → Jinja模板 → CSS响应式布局 / JS图表
```

上传 → 服务端解析 → 错误/重复检查 → 30分钟临时预览 → 用户确认 → 原子入账 → 分类/共享人工核对。

金额不使用浮点存储；转账不计家庭消费；退款按发生月份冲减支出。家庭预算只算共享账单，私有账单在“我”中查看。全家首页与明细“全部可见”口径不同，界面已说明。每个成员只能编辑自己的记录，退出家庭会取消全部个人记录共享。完整参数、字段别名、支持边界及函数输入输出见 [docs/PARAMETERS.md](docs/PARAMETERS.md)。

## 备份与恢复

在正式环境配置下执行（演示数据同理，但需设置对应DATA_DIR）：

```powershell
.\.venv\Scripts\python.exe manage.py backup data/backups/ledger-2026-11-15.sqlite3
```

使用SQLite备份API，可在运行中获得一致快照，目标文件已存在时拒绝覆盖。另行安全保存 `data/secret.key`。CSV导出是明细，不是完整恢复备份。

恢复步骤：停止服务，完整重命名保留当前data目录（含wal/shm），创建新的data目录，将备份复制为 `ledger.sqlite3`，放入原密钥后重启；不要将旧wal/shm文件混入恢复目录。可在独立DATA_DIR中先演练。若密钥丢失可重新生成，账号和账单仍在，但旧登录会话失效。

## 验证与开发规范

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q app run.py manage.py
.\.venv\Scripts\python.exe -m pip check
```

测试覆盖：分金额、转账/退款、家庭共享与私有隔离、CSRF、越权、预算管理员、CSV编码/前言/XLSX、两阶段导入、重复提交、批次撤销、邀请一次性、退出共享撤回、导出公式防护、账单增改删恢复、注册登录、主要页面渲染。测试用临时数据库，不改正式数据。

规范：业务规则集中到services；外部文件适配集中到importer；路由处理HTTP和归属；写操作显式事务；注释说明业务意图；仅在编码回退、逐行外部输入和解析边界捕获具体异常，不用大范围 `except Exception` 掩盖程序错误。当前是初始结构，后续表结构变更应增加编号迁移，不直接修改线上数据库。

验收记录见 `docs/VERIFICATION.md`。2026-11-16已修复非空账本模板缺少上下文的问题，18项自动测试通过；浏览器实际确认首页、账单明细、记一笔表单正常打开。8000正式服务和8001演示服务均已后台启动，跨命令健康检查通过。当前仍未进行真实手机设备验收。

## 当前边界和后续工作

- 尚不具备微信/支付宝个人明细实时自动拉取；依赖主动导出并上传。格式变化需扩展适配器，真实导出样本仍需进一步验收。
- 提醒是站内提示，不具备网页关闭后的通知发送；没有PWA离线同步、邮件、推送或后台定时任务。
- 不含账户余额/资产负债、多人分摊、仅共享汇总、商家规则学习、OCR、语音、多币种、自动退款关联。
- 无密码找回/修改、账号删除和管理员移交界面。适合本机/小家庭试用，正式公网使用需补齐账号生命周期、数据库迁移、运维与浏览器验收。
- SQLite适合小规模单机部署；多实例/高并发再迁移PostgreSQL，当前无需引入额外服务。
