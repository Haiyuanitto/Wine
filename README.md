# Wine-Searcher 勃艮第名庄自动比价

这个工具会自动在 Wine-Searcher 搜索勃艮第名庄酒款，提取报价后用“香港商家报价中位数”作为市场基准价，并筛出更低价的机会。

比价规则：只比较同一酒款的同一年份（vintage）。没有识别到年份的报价不会参与比较。

## 1) 安装

```bash
cd /Users/haiyuanxue/Documents/New\ project
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) 配置

默认配置文件：

- `/Users/haiyuanxue/Documents/New project/config/config.yaml`

你可以修改：

- `famous_producers`: 目标名庄名单
- `queries`: Wine-Searcher 查询词（不填则自动从名庄生成）
- `min_saving_pct`: 至少低于香港基准价多少比例才输出
- `fx_to_hkd`: 汇率（手动维护）

默认配置已预置以下名庄：Domaine Leroy、Armand Rousseau、Domaine de la Romanee-Conti、Emmanuel Rouget、Domaine Leflaive、Rene Engel、Domaine Meo-Camuzet、Arnaud Ente、Domaine d'Auvenay、Comte Liger-Belair、Comtes Lafon、Roumier。
其中 Leroy 仅搜索/匹配 `Domaine Leroy`，并默认排除 `Maison Leroy`。

## 3) 运行

单次运行：

```bash
python3 wine_searcher_bot.py --config config/config.yaml
```

每 6 小时自动轮询一次：

```bash
python3 wine_searcher_bot.py --config config/config.yaml --watch-hours 6
```

## 4) 输出

每次运行会在 `/Users/haiyuanxue/Documents/New project/output` 生成：

- `deals_YYYYmmdd_HHMMSS.csv`
- `deals_YYYYmmdd_HHMMSS.md`

字段包含：

- `baseline_hkd`: 香港商家报价中位数（按同酒款聚合）
- `offer_hkd`: 当前报价换算 HKD
- `saving_hkd` / `saving_pct`: 相比香港基准节省金额/比例
- `merchant`, `location`, `source_url`

## 5) 说明

- Wine-Searcher 页面结构可能变化，若出现抓取不到数据，需要微调 `wine_searcher_bot.py` 中的 CSS 选择器。
- 如果出现反爬或需要登录，可改为“浏览器自动化”版本（Playwright）并带 Cookie。当前版本先以轻量抓取为主。
- 汇率为手动配置，不会自动拉实时汇率。

## 6) 云端部署（GitHub Actions）

项目已内置工作流文件：

- `/Users/haiyuanxue/Documents/New project/.github/workflows/wine-searcher-cloud.yml`

部署步骤：

1. 在 GitHub 新建仓库并推送当前项目代码。
2. 打开仓库 `Settings` -> `Secrets and variables` -> `Actions`。
3. 新建 `Repository secret`：
   - `WINE_CONFIG_YAML`（可选，内容是完整的 `config.yaml` 多行文本）。
4. 打开 `Actions` 页面，手动运行一次 `Wine Searcher Cloud Runner` 验证结果。
5. 之后会按计划自动运行（当前为每 6 小时一次，UTC 时间）。

运行产物：

- 每次任务会在 Actions 的 `Artifacts` 中上传 `output/` 报告文件。

修改频率：

- 编辑 `/Users/haiyuanxue/Documents/New project/.github/workflows/wine-searcher-cloud.yml` 中的 `cron` 即可。
