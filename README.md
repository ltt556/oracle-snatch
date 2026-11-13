# 甲骨文新加坡免费实例蹲机 — GitHub Actions 版

把蹲机放到 GitHub Actions 上定时跑：GitHub 的机器 24 小时在线，不依赖
你自己的电脑开不开机，也不依赖 Muse 那台老被替换的虚拟机。

两个目标一起蹲（每轮先查实例是否存在，查不到才申请，不会重复建）：

- `sg-arm-1`：VM.Standard.A1.Flex，1 OCPU / 6 GB，Ubuntu 24.04 ARM
- `sg-micro-1`：VM.Standard.E2.1.Micro，Ubuntu 24.04 x86

## 两种模式（二选一）

**公共仓库（推荐，覆盖最满）**
- 标准 runner 对公共仓库免费不限量，所以可以每小时触发一次、每次
  循环跑约 50 分钟（5 分钟一轮），基本是连续蹲。
- 代码是公开的，但代码里只有 OCID 这类标识符，没有任何密钥；
  密钥都放在仓库的 Secrets 里（加密存储，外部看不到，fork 的 PR
  也拿不到）。
- 缺点：公共仓库 60 天没有活动，GitHub 会自动停用定时任务。
  本仓库自带 `keepalive` 工作流，每月自动提交一次保活，不用管它。
- 已是默认配置（`.github/workflows/snatch.yml`，cron 为每小时 :07，
  `RUN_MINUTES: '50'`）。

**私有仓库（代码不公开）**
- GitHub Free 每月只有 2,000 分钟 Actions 额度（私有仓库）。
- 必须改成：cron 改 `'*/30 * * * *'`、`RUN_MINUTES` 改 `'0'`
  （每次只试一轮就退出）。约 48 次/天 × 约 1 分钟 ≈ 1,440 分钟/月，
  在免费额度内，但覆盖只有每小时 2 轮。
- 千万别在私有仓库用默认的"每小时 + 50 分钟循环"，一天就烧约
  1,300 分钟，两天就爆额度。

**注意 GitHub 定时的固有毛病**：最短间隔 5 分钟；高峰期定时运行
可能延迟 5–30 分钟、偶尔被丢弃。所以它适合当主力或第二道防线，
和 Muse 那边的蹲机（每 15 分钟有监控恢复、5 分钟一轮）并行跑，
两边都有防重复 guard，偶尔撞上也只是被限流一轮，不会重复建实例。

## 部署步骤

1. 在 GitHub 新建一个仓库（公共或私有，按上面选），把本文件夹的
   全部内容传上去（包括 `.github` 文件夹，注意别漏）。
2. 仓库 → Settings → Secrets and variables → Actions → New
   repository secret，逐个添加下面 7 个 Secret。
3. Actions 页签里手动运行一次 `oracle-snatch`（Run workflow）测试：
   日志里出现 attempt 和 `Out of host capacity` 就是在正常蹲了。
4. 抢到后工作流会自动开一个 Issue 通知你（GitHub 会给仓库主人发
   邮件/App 通知）。看到后告诉 Muse，把另一边的蹲机停掉。

## 邮件通知（抢到 / 没抢到都发）

- 工作流每轮运行结束都会在固定的「蹲机状态通知」Issue 里留一条
  结果评论（抢到了 / 没抢到 / 出错），评论里会 @ 仓库主人，GitHub
  默认会就 @ 提醒发邮件，所以每轮都会有一封结果邮件。
- 邮件发到 **GitHub 账号的主邮箱**。想改收件邮箱：GitHub →
  Settings → Emails 改主邮箱，或 Settings → Notifications 里
  另设通知邮箱。
- 前提：GitHub → Settings → Notifications 里，邮件通知（尤其
  @mentions）是开着的。如果一直收不到，先去那里确认。
- 注意频率：公共仓库模式是每小时一轮，一天最多约 24 封结果邮件。
  嫌多的话告诉 Muse，可改成「每天汇总一封 + 抢到立即发」。

## 需要填的 7 个 Secret

| Secret 名 | 内容 |
| --- | --- |
| `OCI_CONFIG` | 见下方模板，填好用户 OCID 和新密钥的指纹 |
| `OCI_API_KEY` | 新 API 密钥的**私钥**文件全部内容（`.pem`） |
| `OCI_COMPARTMENT_ID` | `ocid1.tenancy.oc1..aaaaaaaajctwiln7vxgkvktxkkbv5xw2dpdy6uhe2f77nqheokl3x2r47dba`（根区间就是租户本身） |
| `OCI_SUBNET_ID` | `ocid1.subnet.oc1.ap-singapore-1.aaaaaaaaqvsbwkq5chtl3jfyuprxfj5fapgwgt2l3fbpvznnh6zz2bdenaka` |
| `OCI_IMAGE_ARM` | `ocid1.image.oc1.ap-singapore-1.aaaaaaaaxvfsj3bvzatjfbtulypvtqjavwbdgmxdrz4tn3nc7xrsvcfejoka` |
| `OCI_IMAGE_X86` | `ocid1.image.oc1.ap-singapore-1.aaaaaaaam22myza4f6cfdztl7ayqfcde32obsvkyqat4xmqjtnb43r3ofp2q` |
| `OCI_SSH_PUBKEY` | `oracle_sg.pub` 文件的内容；想自己也能 SSH 登录的话，把你自己的公钥作为第二行一起贴进去 |

`OCI_CONFIG` 模板（`key_file` 必须写这个路径，工作流就是把私钥
写到这里）：

```
[DEFAULT]
user=<你的用户OCID>
fingerprint=<新API密钥的指纹>
tenancy=ocid1.tenancy.oc1..aaaaaaaajctwiln7vxgkvktxkkbv5xw2dpdy6uhe2f77nqheokl3x2r47dba
region=ap-singapore-1
key_file=/home/runner/.oci/oci_api_key.pem
```

## 关于 API 密钥（重要）

- **不要用 Muse 本地那把私钥**，它不外传。
- 在甲骨文控制台 → My profile → API keys → Add API key，生成一把
  新密钥：私钥下载下来贴到 `OCI_API_KEY`，控制台给的指纹填进
  `OCI_CONFIG`。这一步只能你自己做（私钥只显示一次）。
- 更稳妥的做法（可选）：专门为 GitHub 建一个独立的 IAM 用户 +
  受限策略（只允许在区间内管理实例/用网络），这把钥匙就算泄露也
  影响不到账号其他东西。要走这条路让 Muse 带你配。

## 文件说明

- `snatch_once.py`：蹲机脚本。`RUN_MINUTES=0` 只试一轮；
  `RUN_MINUTES=50` 循环约 50 分钟。每轮先 list 实例做防重复检查。
- `.github/workflows/snatch.yml`：定时触发 + 装环境 + 写密钥 + 跑脚本；
  抢到后自动开 Issue 通知。
- `.github/workflows/keepalive.yml`：每月提交一次，防止公共仓库的
  定时任务被 GitHub 因 60 天无活动停用。
- `requirements.txt`：只装 `oci` SDK。
