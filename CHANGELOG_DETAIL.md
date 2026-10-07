# 改动细节档案（CHANGELOG_DETAIL）

> 本文件存放各版本「怎么改的」实现细节：涉及的文件、测试、踩过的坑。
> 用户向的发布说明见 [CHANGELOG.md](CHANGELOG.md)（只写修复 / 新增 / 怎么用）。

# v6.5.2 — 目录清理支持包含子目录（多级递归）

## 目录清理支持包含子目录（多级递归）

### 现象

`服务 → 目录清理` 只能清掉根目录**下一层**的目录。下载目录一旦有多层嵌套
（`/downloads/站点/剧集/季/剧集文件`），深处的空壳目录、样本目录永远扫不到。

### 原因

`app/helper/clean_helper.py` 的 `scan()` 用 `os.listdir(root_path)` 遍历**直接子项**，
没有任何下钻逻辑；`os.listdir` 只返回一层。

### 改动（7 个文件）

| 文件 | 改动 |
|---|---|
| `app/helper/clean_helper.py` | `scan()` / `clean()` 增加 `recursive` 参数（默认 `True`）；未命中才下钻子目录；命中即剪枝；`matched` / `deleted` 增加 `depth` 字段；新增 `normalize_recursive()` 统一归一化 bool / 字符串 / None（None 取默认 True）；`format_result_message` 按口径区分「各级子文件夹」/「一级子文件夹」 |
| `web/action.py` | `__clean_dirs_scan` / `__clean_dirs_run` 两个入口透传 `recursive` |
| `web/apiv1.py` | `/system/clean_dirs/scan` 与 `/run` 增加 `recursive` 表单参数（`follow_links` 的字符串转 bool 写法照抄） |
| `web/main.py` | 模板变量 `CleanDefaultRecursive`（从 `clean_dirs.recursive` 取） |
| `web/templates/service.html` | 新增「包含子目录」开关（默认勾选，与「跟随符号链接」同一行 `d-flex`）；预览 / 已删列表加「第N层」徽标；`clean_dirs_params()` 带上 `recursive` |
| `config/config.yaml` | `clean_dirs.recursive` 默认 `true` |
| `tests/test_clean_helper.py` | 过时的「只处理一级」用例改为覆盖 **递归 / 剪枝 / 一级** 三种契约；补 `depth` 字段断言 |

### 关键设计：命中即剪枝

父目录命中阈值后其子目录必然随之消失，若继续下钻会：

1. 让 `total_free_bytes` **重复累加**（父 2MB + 子 1MB 被算成 3MB，而实际只释放 2MB）；
2. 在真正删除时产生「子目录已随父删除」的**无效删除**与报错。

所以 `walk()` 命中即 `return True` 表示「已剪枝」，不再下钻。

### 验证

- `.workbuddy/tests/clean_dirs_recursive_verify.py` —— **49/49**
  （真实目录树：多层命中、剪枝、depth、根目录/散落文件不受影响、dry_run、真删除、参数归一化）
- `.workbuddy/tests/clean_dirs_recursive_neg.py` —— 反向 **5/5**
  （关递归 / 去剪枝 / 破坏参数归一化，均被精确检出）
- `.workbuddy/tests/clean_dirs_recursive_ui_verify.py` —— **21/21**（模板 / 接口 / 配置接线）
- 项目官方 `tests/test_clean_helper.py` —— **33 项全绿**（2 项符号链接因 Windows 权限跳过）
- `py_compile` 通过；7 个文件裸 LF 均为 0

### 踩到的坑

- 官方测试用 `unittest` 不是 pytest，隔离 venv 里没装 pytest
  → `python -m unittest tests.test_clean_helper`（需 `export NASTOOL_CONFIG=<repo>/config/config.yaml`）
- 官方测试的 `_mkfile(rel_path, size)` 里 `size` 是**字节数不是 MB**，且只接受 int
  （写 `0.5` 会 `TypeError: can't multiply sequence by non-int of type 'float'`）
- `matched[].path` 是**绝对路径**且 Windows 下分隔符是 `\` → 断言深层目录要用 `endswith`，
  不能拿相对串去 `assertIn`
- `clean_helper` 是 `import log` 后调**模块级** `log.info(...)`，桩件只挂 `log.log` 对象会
  AttributeError，须逐个 `setattr(log_mod, "info", ...)` 挂模块级函数

# v6.5.1 — 媒体库设置页去掉「动漫」「未识别」两张卡

## 背景

「媒体库」页（`web/templates/setting/library.html`）是**一级目录根**的配置入口，
四张卡分别绑 `media.movie_path` / `media.tv_path` / `media.anime_path` / `media.unknown_path`。

v6.5.0 把动漫降为二级分类之后，后两张与当前模型不再匹配：

| 卡 | 后端配置 | 实际作用 | 去掉的理由 |
|---|---|---|---|
| 动漫 | `media.anime_path` | 动漫剧集是否单独一个物理根（留空则回落 `_tv_path`） | v6.5.0 之后动漫住在剧集根**下面**的「动漫剧集」二级目录，不再需要独立根入口 |
| 未识别 | `media.unknown_path` | 识别失败文件的**备份硬链接**落点 | 它是备份目录、不是媒体库，挂在「媒体库」页语义不符 |

## 改动（1 个文件）

| 文件 | 改动 |
|---|---|
| `web/templates/setting/library.html` | 删掉「动漫」「未识别」两张卡，239 行 → 157 行 |

## 后端零改动（重要）

`media.anime_path` / `media.unknown_path` 两个配置键、`__update_directory` 接口，
以及各处读取它们的代码**全部保留**：

| 读取点 | 行为 |
|---|---|
| `app/filetransfer.py:106-107` | `anime_path` 留空 ⇒ 动漫根回落 `_tv_path`，`_anime_category_flag` 回落 `_tv_category_flag` |
| `app/filetransfer.py:1319 / 1362` | `meta_info.type == ANIME` 时仍取 `_anime_path`（手工配了值照样生效） |
| `app/filetransfer.py:730-735` | 识别失败时若配了 `unknown_path` 才转移备份，否则只记一条记录 |
| `web/main.py:1165-1170`、`web/action.py:3705 / 4438` | 文件管理目录树、空间统计仍把这两个键算进去 |
| `app/plugins/modules/libraryscraper.py:53-54` | 媒体库刮削仍把 `anime_path` 计入待刮目录 |

⇒ **纯 UI 收口**：只是不再从界面提供入口，能力与数据一个没丢。

## 验证

- `_verify_651.py`：模板结构断言（两张卡已无、电影 / 电视剧卡保留、JS 函数完好、
  `anime_path` / `unknown_path` 不再出现在模板里）+ 后端引用完整性 + 升版锚点
- 回归：`_verify_650.py`（分类策略）/ `_verify_649.py` 等

# v6.5.0 — 动漫降为二级分类 + 默认策略换成定稿方案

## 背景

定稿的模型是：**一级目录只对齐 TMDB 的 `media_type`（movie / tv 两个物理根）**，
「动漫」不再是一个**类型 / 物理根**，而是**各自根下面的一个二级分类**
（电影端的「动漫电影」、剧集端的「动漫剧集」）。

关键点是：**「把 `anime:` 段从策略里删掉」并不等于「动漫降级」**。分派点在
`app/media/meta/_base.py:577-580`：

```python
if self.type == MediaType.TV:
    self.category = self.category_handler.get_tv_category(info)
else:
    self.category = self.category_handler.get_anime_category(info)   # ANIME 只走这一支
```

`get_anime_category` 读的是 `_anime_categorys`（即策略文件的 `anime:` 段）。段一删，
它拿到的分类名就是 **空字符串**，路径变成 `os.path.join(tv_path, "", dir_name)`
⇒ 动漫剧集**全部平铺在剧集根目录下**，一个分类都不进（实测见「验证」第 6 组）。

所以「删 `anime:` 段」必须与「`anime:` 段缺失时回落到 `tv:` 段」**同时成立**，
动漫才会落进剧集端的二级分类里。

> ⚠️ 这里删的只是**配置里的 `anime:` 段**。`MediaType.ANIME` 这个枚举**保留不动**——
> 它退化成"物理根开关"（`filetransfer.py:106-107`：`anime_path` 留空 ⇒ 根回落 `_tv_path`、
> `_anime_category_flag` 回落 `_tv_category_flag`），删掉它只会白丢「动漫可独立成库」与
> 「站点 anime 路径」两个选项，换不到任何分类收益。

## 改动（3 个文件）

| 文件 | 改动 |
|---|---|
| `app/media/category.py` | `init_config` 增 4 行：策略里没有 `anime` 段时，`_anime_categorys` 别名 `_tv_categorys` |
| `config/default-category.yaml` | movie 段 4 档、tv 段 5 档，见下 |
| `config/my-category.yaml` | **删除**（内容并入默认策略，仓库只留一份） |

```python
            self._anime_categorys = self._categorys.get('anime')
            # 动漫（ANIME，即 genre 含 16 的剧集）与剧集共用二级分类，不再单列：
            # 策略文件里不写 anime 段时沿用 tv 段，避免分类名变成空字符串后
            # 动漫剧集平铺在剧集根目录下（一个分类都不进）。
            if not self._anime_categorys:
                self._anime_categorys = self._tv_categorys
```

## 为什么改 category.py 而不是 _base.py

| 方案 | 评价 |
|---|---|
| 改 `_base.py` 让 ANIME 调 `get_tv_category` | 只修了「分类名」一处；`anime_category_flag`、`anime_categorys`（下载目录设置页那张「动漫」下拉的数据源，`web/action.py:3591`）仍走 `anime` 段，会各自失配 |
| **改 `category.py` 一处别名（采用）** | 三个消费者（`get_anime_category` / `anime_category_flag` / `anime_categorys`）**一并跟着走 tv**；`_base.py` 一行未动 ⇒ ANIME 的识别、命名、刮削规则完全不受影响 |

别名只在「策略里没有 `anime` 段」时触发 ⇒ **向后兼容**：想恢复单列，把 `anime:` 段写回策略文件即可。

## 追加的默认策略

```yaml
movie:                                       # 儿童优先
  儿童电影:  { genre_ids: '10751' }          # 电影端只有 Family，10762 是剧集端的
  动漫电影:  { genre_ids: '16' }             # 秒杀儿童之后：儿童动画电影已归「儿童电影」
  # ★ 纪录片(99) / 音乐(10402) 都不单独成档，按语种落「华语电影」/「外语电影」。
  华语电影:  { original_language: 'zh,cn,bo,za' }
  外语电影:                                   # 兜底，必须最后

tv:                                          # 动漫（ANIME）共用本段；儿童优先
  儿童电视剧: { genre_ids: '10762' }          # 儿童动漫（16+10762）也落这里
  动漫剧集:   { genre_ids: '16' }             # 国漫、日漫统一落这里，不按产地散
  综艺:       { genre_ids: '10764,10767' }    # 真人秀 + 脱口秀
  # ★ 纪录片(99) 不单独成档：直接落「国产剧」/「外语电视剧」，混在正常分类里便于发现后手删。
  国产剧:     { origin_country: 'CN,TW,HK' }
  外语电视剧:                                  # 兜底，必须最后
```

三条铁律（配错是**静默**的：不报错、只是分错）：① 顺序即优先级、命中即停；
② 空 value 项 = 兜底（`if not item: return key`），必须放最后；
③ `genre_ids` 在电影端与剧集端是两套编号，只有 `16`（动画）两端同号。

★ **两端都遵循「儿童优先」**：儿童电影(10751) 排在动漫电影(16) 之前、儿童电视剧(10762) 排在
动漫剧集(16) 之前 ⇒ 儿童动画电影归「儿童电影」、儿童动漫归「儿童电视剧」；
剩下只有 `16`（没有 Kids/Family 标签）的才进「动漫电影」/「动漫剧集」。

## 落盘结果（不配 `anime_path`，共 2 个物理根）

| 内容 | 落点 |
|---|---|
| 儿童动画电影（16 + 10751） | `movie_path/儿童电影` |
| 其余动画电影（含国漫电影、日漫电影） | `movie_path/动漫电影` |
| 华语非动画电影 | `movie_path/华语电影` |
| 音乐片 | `movie_path/华语电影` / `movie_path/外语电影`（按语种） |
| 华语纪录片 | `movie_path/华语电影` |
| 外文纪录片 | `movie_path/外语电影` |
| 儿童动漫（汪汪队、小猪佩奇） | `tv_path/儿童电视剧` |
| 国漫（诛仙、凡人修仙传） | `tv_path/动漫剧集` |
| 日漫 | `tv_path/动漫剧集` |
| 综艺（真人秀 / 脱口秀） | `tv_path/综艺` |
| 国产纪录片 | `tv_path/国产剧` |
| 外文纪录片 | `tv_path/外语电视剧` |
| 非动画国产剧（狂飙） | `tv_path/国产剧` |
| 非动画外语剧（怪奇物语） | `tv_path/外语电视剧` |

## 验证

`_verify_650.py`：用桩顶掉 `log` / `config` 后**真跑** `Category.init_config()` 与三个
`get_*_category()`（`ruamel.yaml` 为真实依赖），在临时目录里模拟 `inner` / `user` 两个目录，
**63/63** 通过。

| 组 | 覆盖内容 | 结果 |
|---|---|---|
| 0 | 源码与策略文件的静态断言（别名恰一处、策略无顶层 `anime:`、含动漫电影/动漫剧集、无「其它剧集」、无纪录片档） | ✓ |
| 2 | 段结构与顺序：movie 4 项、tv 5 项；两端都是「儿童优先」 | ✓ |
| 3 | 别名生效：`anime_categorys == tv_categorys`、两个 flag 相等、`get_anime_category == get_tv_category` | ✓ |
| 4 | 18 条分发用例 + 7 条 ★ 命题：**国漫/日漫同落「动漫剧集」**、儿童动漫落「儿童电视剧」、动漫不被国产剧/外语电视剧抢走、非动画内容仍归各自档 | ✓ |
| 5 | **反向对照 A**：策略里写回 `anime:` 段 ⇒ 别名不触发（配置优先） | ✓ |
| 6 | **反向对照 B**：剥掉别名的旧 `category.py` + 新策略 ⇒ 动漫分类名为空串（**证明这一行不是可选项**） | ✓ |
| 7 | 零回归：顺序即优先级、兜底、`tmdb_info` 为空不抛异常、复制出的文件逐字节一致 | ✓ |

## 升级须知

`/config/default-category.yaml` 是宿主机上的**存量文件**，而 `category.py:32` 的
`if not os.path.exists(self._category_path)` 守卫只认「文件在不在」⇒ **不会自动覆盖**。

因此 NAS 上要先删掉它（或把新内容粘进去）再重启容器，新策略才会生效。
代码侧的改动不受此影响 —— 它随镜像走，`docker compose pull` 后即生效。

## 已知边界

- **纪录片 / 音乐已不再单独成档**：直接落常规分类（电影端 华语 / 外语，剧集端 国产 / 外语），
  混在正常分类里便于发现后手工删除。分类配置**只能决定进哪个目录，不能决定转不转**；
  真要"不转移"只能用「基础设置 → 转移忽略词」（`media.ignored_paths` / `media.ignored_files`，
  正则），而它**只按文件名 / 路径匹配、不看 TMDB 类型**，无法按"纪录片"这种类型精准拦截。
- **动漫仍可单独成库**：`MediaType.ANIME` 枚举保留，配 `anime_path` 即可让动漫剧集落到
  独立物理根（此时它的二级分类名仍取自 `tv:` 段）。「不单列成一级」与「独立成库」两条线是正交的。
- **分类不回溯**：只在转移那一刻算一次，库里已有的存量文件要手工归位。
- 若想恢复动漫独立分类，把 `anime:` 段写回策略文件即可（无需改代码）。
- **仓库里已不存在「不建库的桶」**：纪录片(99)、音乐(10402) 的特殊档全部移除，
  电影端 4 档、剧集端 5 档全部指向真实媒体库，媒体库里看到的就是盘上全部。

# v6.4.9 — 二级分类策略「同名模板优先」兜底

## 背景

v6.4.8 随程序附赠了 `config/my-category.yaml`，但启用时发现：把 `media.category` 改成
`my-category` 之后，生成出来的却是默认策略的内容。

根因在 `app/media/category.py:32-35` —— 策略文件不存在时的兜底复制是**写死的**：

```python
shutil.copy(os.path.join(Config().get_inner_config_path(), "default-category.yaml"),
            self._category_path)
```

不管 `category_name` 叫什么，复制的都是 `default-category.yaml`
⇒ 自定义策略名**永远拿不到自己的模板**，只能拿到 default 的一份副本。

这也解释了为什么「把模板改名成 `default-category.yaml` 就行」并不成立：
存量容器的 `/config/default-category.yaml` **早就存在**，而这段逻辑外面有
`if not os.path.exists(self._category_path)` 守卫
⇒ 已存在的文件永远不会被重新复制，改名对存量用户无效。

## 改动（1 文件，3 行）

`app/media/category.py` —— 兜底复制改为「同名优先、default 回退」：

| 顺序 | 候选模板 | 命中条件 |
|---|---|---|
| ① | `{category_name}.yaml` | 程序目录里存在与策略同名的模板 |
| ② | `default-category.yaml` | ① 不存在时回退 |

日志同步带上实际使用的模板名（`已按模板 my-category.yaml 生成...`），便于确认复制了哪一份。

**未改动**：`if not os.path.exists` 守卫保留 ⇒ 存量策略文件依旧不会被覆盖；
`Category.get_category` 的判定逻辑、三个 `*_categorys` 属性的取用方式均未动。

## 容器内的两个目录（为什么需要这一步）

| 目录 | 角色 | 内容 |
|---|---|---|
| `/nas-tools/config` | 镜像内源码目录（`Config().get_inner_config_path()`） | 程序自带的模板，随镜像更新 |
| `/config` | 宿主机挂载的配置目录（`NASTOOL_CONFIG`） | 用户实际生效的策略文件，长期保留 |

两者是不同目录，「同名复制」正是连接它们的那一步。

## 验证

`_verify_649.py`：用桩顶掉 `log` / `config` 后**真跑** `Category.init_config()`（`ruamel.yaml`
为真实依赖），在临时目录里模拟 `inner` / `user` 两个目录，**29/29** 通过。

与 v6.4.8 的 `_verify_category_scheme.py` 不同 —— 那个复刻了判定逻辑，这次是直接执行源码，
覆盖的是文件系统行为。

| 用例 | 断言 | 结果 |
|---|---|---|
| ① 有同名模板 | 生成内容 == `my-category.yaml`，且 ≠ default | ✓ |
| ② 无同名模板 | 回退 == `default-category.yaml` | ✓ |
| ③ 默认名 | 行为与修复前逐字节一致（向后兼容） | ✓ |
| ④ 文件已存在 | 内容不被覆盖（存量用户保护） | ✓ |
| ⑤ 解析结果 | movie / tv / anime 三段非空，首项均为「儿童」类 | ✓ |
| ⑥ 反向对照 | 旧逻辑下同一场景确实拿到 default 的内容 | ✓ |
| ⑦ 边界 | 策略名为空不产生文件；名为 `config` 被拒绝 | ✓ |

## 已知边界

- **只影响「文件不存在」的那一刻**：已有的策略文件（含 v6.4.8 之前生成的）不会被替换，
  存量用户想换成新模板仍需手动覆盖一次。
- **策略名仍是自由输入**：界面上没有「下拉选择已有策略」的控件，本次不改前端；
  名字打错时会静默回退到 default 模板（日志有提示，但界面不报错）。

# v6.4.8 — 附赠经验证的二级分类策略模板（config/my-category.yaml）

## 背景

自己写 `media.category` 有三类坑，而且**配错是静默的** —— 不报错、只是分错目录，排查成本很高：

1. **顺序写反**：`get_category` 是平表遍历、命中即停（`app/media/category.py:145`），
   儿童动画电影同时带 `16` 与 `10751`，若「动漫」写在「儿童」前面就会被抢走；
2. **兜底项位置错**：空 value 项等于 `return key`（`:146`），放中间会让后面的规则永远轮不到；
3. **题材编号串台**：TMDB 电影端与剧集端是**两套独立编号表**，
   儿童电影只有 `Family(10751)`（电影端没有 `Kids`），儿童电视剧才是 `Kids(10762)`，
   照抄一个数字到另一段会永远匹配不到。

因此把一套按真实 TMDB 字段逐条验证过的策略随程序附赠，改一个 `category` 名字即可启用。

## 改动（新增 1 文件 + 3 文件升版）

| # | 文件 | 改动 |
|---|---|---|
| ① | `config/my-category.yaml` | **新增**。movie / tv / anime 三段完整策略 + 落地说明，纯 LF |
| ② | `version.py` | `APP_VERSION` v6.4.7 → v6.4.8 |
| ③ | `CHANGELOG.md` | 头部插入 v6.4.8 段 |
| ④ | `README.md` | 版本行 / `docker pull` / Last updated |

**零代码改动** —— 不动 `app/` 下任何文件，纯配置交付，因此没有回归风险。

## 策略要点

| 段 | 顺序（命中即停） | 条件 |
|---|---|---|
| `movie:` | 儿童电影 → 动漫电影 → 其他电影 → 华语电影 → **外语电影**（兜底） | `10751` / `16` / `99,10402` / `original_language: zh,cn,bo,za` / 空 |
| `tv:` | 儿童电视剧 → 其他剧集 → 国产剧 → **外语电视剧**（兜底） | `10762` / `10764,10767,99` / `origin_country: CN,TW,HK` / 空 |
| `anime:` | 儿童电视剧 → **动漫电视剧**（兜底） | `10762` / 空 |

## 关键设计

- **动漫不占独立物理根**：不配 `anime_path` 时，`anime` 段产出的目录落在 `tv_path` 下。
  让 `tv` 段某个分类与 `anime` 段分类**同名**（本案的「儿童电视剧」），两段结果**自动合流**到同一目录
  ⇒ 真人儿童剧与儿童动漫剧物理上真的在一起。
- **动漫仍可单独成库且不重复扫描**：媒体服务器侧的「剧集」库只指向 `国产剧` + `外语电视剧`
  （**不指 `tv_path` 根**），`动漫电视剧` 就不在它的扫描范围内。
  这两件事在配了 `anime_path` 的形态下**互斥**（儿童动漫剧会跟着跑进 `anime_path`，儿童被拆两个根）。
- **「其他」桶挡在语言 / 地区条件之前**：综艺的 `origin_country` 多为 `CN`，
  若排在「国产剧」之后会被吃掉，国产剧目录里就会混进综艺。
- **动画电影走 `movie` 段**：`__get_tmdb_type`（`app/media/meta/_base.py:647`）只在 `TV` 分支里按
  `ANIME_GENREIDS = ['16']` 二次判为 ANIME，电影分支返回原始 `movie` ⇒ `anime` 段只管动漫剧集。

## 验证

离线验证脚本 `_verify_category_scheme.py`：复刻 `Category.get_category` 的判定逻辑（逐字对齐
`category.py:133-175`），对典型影片跑分发。

| 层 | 内容 | 结果 |
|---|---|---|
| 分发用例 | 23 条，含各段兜底与边界情形 | **23/23** ✓ |
| 结构断言 | 段数量、首项为儿童、末项为空值兜底、两端编号正确、两段儿童同名、电影端未误用 10762 | **14/14** ✓ |

用例选摘：

| 影片 | 判定段 | 落点 |
|---|---|---|
| 玩具总动员（16+10751, en/US） | movie | 儿童电影 |
| 千与千寻（16+10751, ja/JP） | movie | 儿童电影 |
| 你的名字（16+10749, ja/JP） | movie | 动漫电影 |
| 地球脉动（99, en/GB） | movie | 其他电影 |
| 无间道（80+18, zh/HK） | movie | 华语电影 |
| 肖申克的救赎（18, en/US） | movie | 外语电影 |
| 汪汪队（按 tv 判 / 按 anime 判） | tv / anime | 儿童电视剧（两段同结果） |
| 奔跑吧（10764, zh/CN） | tv | 其他剧集 |
| 庆余年（18, zh/CN） | tv | 国产剧 |
| 鱿鱼游戏（53+18, ko/KR） | tv | 外语电视剧 |
| 诛仙 / 凡人修仙传（16, zh/CN） | anime | 动漫电视剧 |

## 已知边界

- **分类不回溯**：分类只在文件**转移那一刻**计算一次（`app/filetransfer.py`），
  改完策略后媒体库里已有的文件不会自动重排，需手动归位。
- **儿童电影精度弱于儿童电视剧**：TMDB 电影端没有 `Kids(10762)`，只有 `Family(10751)`，
  会误收少量成人向家庭片；剧集端 `10762` 准得多。上游题材表的固有差异，非配置问题。
- **要看到独立入口需建库**：媒体服务器把库内容平铺展示，分类目录不会自动变成界面分组，
  需要为每个分类目录建一个库（或用多路径覆盖）。
- **「精选」会插队**：在 Emby 里点过星标的片子会被夺到 `movie_path/精选/`，
  查重时优先于普通分类（`app/filetransfer.py:1211`）。

# v6.4.7 — 片名识别两处修复（^JADE 加中文约束 / 中文名去重）

## 背景

用户实测：文件名 `[诛仙].Jade.Dynasty.S04E07…mkv` 被识别成「豪门恩怨 / 1981 美剧」。
排查确认**片名在查 TMDB 之前就被改坏**，与 TMDB 侧无关。两个互相独立的缺陷：

1. `_name_nostring_re` 的 `^JADE` 分支把 `Jade Dynasty` 的 `Jade` 当台标削掉 ⇒ 只剩 `Dynasty`；
2. `__init_name` 会把**第二个中文 token 拼接**进 `cn_name` ⇒ `[诛仙].诛仙…` 变成 `诛仙 诛仙`，
   而 TMDB 中文名要求**完全相等** ⇒ 匹配失败。

## 改动（2 文件 3 处）

| # | 文件 | 位置 | 改动 |
|---|---|---|---|
| ① | `app/media/meta/metavideo.py` | `_name_nostring_re` | `^JADE` → `^JADE(?=[\s._\-]*[\u4e00-\u9fff])` |
| ② | `app/media/meta/metavideov2.py` | `_name_nostring_re` | 同上（增强识别 V2 分支同病） |
| ③ | `app/media/meta/metavideo.py` | `__init_name` | 追加中文 token 前加 `token not in self.cn_name.split()` |

## 关键设计

- ① 的零宽先行断言 `(?=...)` 是「**只看一眼后面、不消耗字符**」⇒ `JADE` 后紧跟分隔符 + 汉字才认定为台标。
- ② 与 ① 必须同时改：开关 `laboratory.recognize_enhance_enable` 决定走 v1 还是 v2，只改一处会半边失效。
- ③ 修的是**独立于自定义识别词**的缺陷：`[斗罗大陆]斗罗大陆…`、`[鬼灭之刃]鬼灭之刃…` 现状一律拼成 `X X`。

## 验证

| 层 | 内容 | 结果 |
|---|---|---|
| 专项 | `_verify_647.py` 40/40（含**反向对照**：HEAD 版本确实复现两个病；`^ViuTV` 无效性证明） | ✓ |
| 回归 | `_verify_skipexists.py` 51/51 ｜ 643 54/54 ｜ 642 57/57 ｜ 641 35/35 ｜ 640 55/55 ｜ layout_645b 21/21 ｜ trimemedia 26/26 ｜ ugreen636 52/52 ｜ log_api 7/7 | ✓ |

## 踩坑 / 如实记录

- **`^ViuTV` 加中文约束是无效改动**：`_name_nostring_re` 里另有一条 `^[A-Z]{1,4}TV[\-0-9UVHDK]*` 分支，
  `ViuTV` = `Viu`(3 字母) + `TV` 正好被它匹配，而它在 alternation 中排在 `^ViuTV` 之后 ⇒
  `^ViuTV` 断言失败时照样被削。故**不加**（加了是死代码）。
- 代价：`JADE.<英文剧名>`（裸台标 + 英文，如 `JADE.The.Flash`）不再剥 `JADE`，片名变 `Jade The Flash`。
  判断风险很低 —— Jade 是 TVB **中文**频道，裸 `JADE.` 后几乎总是中文剧名；
  方括号写法 `[JADE] The Flash` 完全不受影响（走发布组名单）。
- `_verify_644.py` 掉到 49/2 —— 两条 FAIL 是它绑定的「v6.4.4 当时工作区快照」断言
  （改动面正好 5 个文件 / `web/action.py` 与 v6.4.4 备份一致）。
  **已用「临时还原改动后重跑」证明与本次改动无关**（还原后同样 49/2）。

## 已知边界

- `ViuTV <英文剧名>` 仍会被削成英文部分（`^[A-Z]{1,4}TV` 未加约束）——
  本次刻意不动、避免扩大改动面；若要修需给 `^[A-Z]{1,4}TV` 一并加中文约束。

# v6.4.6 — 刷流弹窗版式重排（限速下线 / 跳过已入库挪位 / 包含排除还原）

## 背景

v6.4.5 上线后用户实际使用反馈三条：

1. 「包含 / 排除」合并成 `T=` / `F=` 单框**不如两个独立框直观** ⇒ 要求还原；
2. 「跳过已入库」落在选种规则区末行**不够醒目** ⇒ 要求挪到顶部开关行；
3. 「限免到期下载限速」**无实际用途** ⇒ 要求下线。

## 改动（仅 1 个文件）

`web/templates/site/brushtask.html`（`+23/−89`，1462 → 1396 行）

| # | 改动 | 说明 |
|---|---|---|
| 1 | 「限免到期下载限速」下线 | markup + 清空 / 回填 / 读值 / 提交 **五处接线全删** ⇒ 全文 0 命中 |
| 2 | 「跳过已入库」挪到顶行 | 占原限速位置：`消息推送 → 转移到媒体库 → 跳过已入库 → 限免到期删种` |
| 3 | 「包含 / 排除」拆回两个独立框 | 连同 `brush_filter_parse` / `brush_filter_join` 两个函数一并删除 |

**后端 3 文件（`action.py` / `apiv1.py` / `brushtask.py`）零改动** ——
提交仍是 `brushtask_include` + `brushtask_exclude` 两个 key；选种规则区回到 **9 卡 3×3 整行**。

## 关键设计

- 「包含 / 排除」还原用的是 **v6.4.5 合并前那份备份**（`backup/code-includeexclude_20261004-1750/`）
  **逐字节搬回**两卡，不是重写的 ⇒ 与合并前完全一致（验证脚本已比对确认）。
- 顺带保留了 v6.4.5 的一个既有修复：新建任务时**仍会显式清空** 包含 / 排除（原版没清、会残留上次的值）。
- 删种开关 tooltip 原文写「**优先级高于限免到期下载限速**」，该开关已不存在 ⇒
  改成「仅作用于还在下载中的种子，已下载完成在做种的种子不受影响」。

## 警告：已知副作用

`web/action.py:2170` 是 `'Y' if data.get("brushtask_free_limit_speed") else 'N'`，
而 `update_brushtask` 是**全量覆盖** ⇒ 界面下线后，网页端保存任务时该字段**恒写 `'N'`**。
DB 列、后端逻辑、`apiv1` 参数都保留（API 仍可传）；
**但老任务此前若存过 `'Y'` 且一直没重新保存，它仍会限速**，需进编辑页保存一次才会归零。

## 验证

| 层 | 内容 | 结果 |
|---|---|---|
| 结构 + 逐字节 | `_verify_layout_645b.py` 21/21（含与合并前备份**逐字节比对**恢复出的两卡 + `node --check` 全量内联 JS） | ✓ |
| 回归 | `_verify_skipexists.py` 51/51 ｜ 643 54/54 ｜ 642 57/57 ｜ 641 35/35 ｜ 640 55/55 ｜ trimemedia 31/31 ｜ ugreen636 52/52 ｜ log_api 7/7 | ✓ |
| 界面 | 项目自带 `tabler.min.css` 无头 Edge 渲染（顶行 4 开关 + 选种规则 3×3） | ✓ |

`_verify_release_644.py` 掉到 67/77 —— 10 个 FAIL 全是「当前树已不是 v6.4.4」的**快照断言**
（version.py / README 版本行 / 首个版本段 / 小节数 / 提交链），与本次布局无关。

## 踩坑

- **提交行 `brushtask_skip_exists: brushtask_skip_exists` 计 2 次**（key + value）⇒
  按「5 处」计数必然误判，正确是 **6**。
- 切片起点取 `id="brushtask_sendmessage"` 会**落在 `<input>` 标签内部**、漏掉第一个复选框 ⇒
  改为从容器开头或按 id 位置排序断言。

## 已知边界

- 后端字段 `brushtask_free_limit_speed` 保留（DB 列 / `apiv1` 参数 / 判定逻辑都在），只是界面不再暴露。
- 限速逻辑本身未删（`brushtask.py` 的 `set_downloadspeed_limit(..., 1)` 分支仍在），
  只是没有界面入口 ⇒ 老任务仍可能触发（见上「已知副作用」）。

# v6.4.5 — 刷流「跳过已入库」+「包含/排除」合并为单输入框

## 背景

用户提出：「刷流时，发现有很多是库里已经有的同名的，就不要下载了」。两轮澄清后定下三个口径：

| 维度 | 口径 | 理由 |
|---|---|---|
| 判断依据 | **媒体库目录**（纯本地扫描） | 不查 TMDB、零额外网络请求；依赖已配好的「媒体库同步目录」 |
| 匹配粒度 | 电影「片名 + 年份」；剧集**精确到季 / 集** | 库里已有 S04E01–E09 就只跳这几集，缺的集仍下载 ⇒ 误杀最少 |
| 季号对齐 | 发布季号 ≠ 库季号时，**用 TMDB 对齐一次** | 应对「发布方第四季 / TMDB 季划分」不一致 |

随后用户追加：「把包含和排除合并，包含用 T=、去除用 F=，这样可以不多一列」——
加「跳过已入库」后选种规则区从 9 变 10 卡（3×3 变末行孤列），合并输入框即可回到 3×3。

## 生效点

`app/brushtask.py::BrushTask.__check_rss_rule(...)`（唯一调用点 `brushtask.py:341`），
返回 `True` = 放行、`False` = 拦截。整段被 try/except 包住、末尾 `return True` ⇒ **fail-open**。
新判断插在 `return True` **之前** —— 即使本判断异常，也不影响上面的既有规则。

规则值编码：`rss_rule` 字典 `str()` 后存 `SITE_BRUSH_TASK.RSS_RULE`，读出 `eval()`
⇒ **新增键零数据库迁移**。

## 改动（4 文件）

| 文件 | 位置 | 改动 |
|---|---|---|
| `app/brushtask.py` | `__check_rss_rule` 末尾 | `if rss_rule.get("skip_exists"): if self.__is_media_exists(title=title): return False` |
| `app/brushtask.py` | 新增 4 类属性 + 7 方法 | 见下 |
| `web/action.py` | `__add_brushtask` | 读 `brushtask_skip_exists` → `rss_rule["skip_exists"]`；徽章加「跳过已入库」 |
| `web/apiv1.py` | `BrushTaskUpdate.parser` | 新增 `brushtask_skip_exists`（Y/N） |
| `web/templates/site/brushtask.html` | 选种规则区 | 开关卡片 + 含/排合并 + `brush_filter_parse/join` |

### `app/brushtask.py` 新增结构

- `_LIBRARY_DIR_RE` / `_LIBRARY_SEASON_RE` 两个编译好的正则
- `__get_library_index()` — 扫 `movie_path` / `tv_path` / `anime_path`，兼容二级分类开/关两种布局，300s TTL 缓存
- `__index_library_dir(...)` — 建索引，排除季目录与 `RMT_FAVTYPE`
- `__find_library_entry(meta_info)` — 按 `get_name()` 查，电影只搜 movie、剧集搜 tv/anime，年份优先
- `__get_library_seasons(entry_path)` — 返回 `{季号: 路径}`
- `__get_library_episodes(season_path)` — 只解析 `RMT_MEDIAEXT` 文件，返回集号集合
- `__align_season_by_tmdb(meta_info, library_seasons)` — 季号对齐
- `__is_media_exists(title)` — 总判定

## ★ 关键设计：为什么用轻量正则而不是 MetaInfo

`MetaInfo` 纯本地解析、不查 TMDB 时 **`title` 恒为 `None`**（`title` 只在 `set_tmdb_info` 里赋值），所以：

- 扫片名层只能用轻量正则 `^(?P<name>.+?)\s*[\(\[]\s*(?P<year>(?:19|20)\d{2})\s*[\)\]]$`；
  实测 **0.009s / 925 目录**，而用 `MetaInfo` 解析 846 个目录要 **8.00s**（快约 800×）。
- **不能复用 `get_no_exists_medias` / `get_moive_dest_path`**：前者内部依赖
  `meta_info.title`（`media_server.py` 第 1311 行）；后者的 `get_format_dict()` 用 `media.title`
  做 `{title}` 模板，纯本地解析会渲染成空 ⇒ 命中率极低。

## 季号对齐语义

`__align_season_by_tmdb(meta_info, library_seasons)`：

- TMDB **有**该季 ⇒ 返回 `None`（库里确实缺，**放行**）；
- TMDB **没有**该季 ⇒ 取 `max(tmdb_seasons)`，若该季在库中则返回它，否则 `None`。

## 判定矩阵（`__is_media_exists`）

- 电影：同名即 `True`
- 剧集：无 `begin_season` ⇒ `False`；查库季；缺该季 ⇒ `__align_season_by_tmdb`
- `__get_library_episodes` 为空 ⇒ `False`
- `begin_episode is None`（整季包）⇒ `True`
- 否则 `get_episode_list().issubset(episodes)`
- 整体 `try/except` ⇒ `False`（放行）

## 「包含/排除」合并（只改 html，后端 3 文件零改动）

合并框在前端解析成 `brushtask_include` / `brushtask_exclude` 后**仍按原 key 提交**
⇒ `action.py` / `apiv1.py` / `brushtask.py` 一行未动。

- 多段用 **`|` 连接**而不是空格：后端 `brushtask.py:1000-1006` 是 **`re.search` 正则**，
  空格会变成「字面空格」而不是「或」。
- 编辑旧任务用 `brush_filter_join()` 拼成 `T=… F=…` 回显，保存后原值不变（round-trip 已验证）。

## 验证

| 层 | 内容 | 结果 |
|---|---|---|
| 真机（NAS） | 改后 `brushtask.py` 投放容器 + 真 `Config`/`MetaInfo`/`Media`/真实媒体库；7 用例 | **全部符合期望** |
| ↳ 用例 | S04E05→放行 ｜ S03E05→跳过 ｜ S03E99→放行 ｜ S02E05→跳过 ｜ S01E01-E26→跳过 ｜ S04 整季包→放行 ｜ S03 整季包→跳过 | ✓ |
| ↳ 索引 | 927 片名 / 934 目录，构建 0.046s | ✓ |
| 离线 | `_verify_skipexists.py` 51/51（结构 17 + 索引 13 + 判定矩阵 10 + 边界 3 + 开关 6…） | ✓ |
| 界面 | `_verify_includeexclude.py` 57/57（含 `node --check` 全量内联 JS） | ✓ |
| 补丁 | 15 锚点干跑 27/27、落盘 31/31（回读逐字节一致）；`py_compile` 3 文件 OK | ✓ |
| 回归 | `_verify_644.py` 49/51 ｜ `_verify_release_644.py` 76/77 ｜ `_verify_log_api_usage.py` 7/7 | 3 个 FAIL 均为「面向发布时刻的快照断言」，非真实回归 |

## 踩坑

- **一度误判「季号错位」**：只看库目录（诛仙只有 Season 1/2/3）就下了结论。跑完整识别链路后更正：
  TMDB 该剧**确实有第 4 季**、识别结果 `begin_season=4` 与发布方一致 ⇒ 诛仙 S04 库里确实没有、
  放行才是对的。若按「忽略季号」的方案反而会误杀。**结论必须来自完整链路，不能只看中间产物。**
- **容器内源码是旧版**：真机验证脚本从容器 `/nas-tools/app/brushtask.py` 抽 AST 时
  `KeyError: '_LIBRARY_DIR_RE'`。SFTP 被 chroot、base64 会撑爆命令行 ⇒ 最终用
  **ssh stdin 管道** `docker exec -i nas-tool sh -c 'cat > /tmp/_bt_new.py'` 投放后 `BT_SRC=/tmp/_bt_new.py`。
- **Git Bash 会 mangle `/tmp/...`** 成 Windows 路径 ⇒ 加 `MSYS_NO_PATHCONV=1`。
- **无头 Edge 截图在沙箱内静默失败**（进程起来但不产 PNG）⇒ 关沙箱才出图。
- **`setattr(instance, ...)` 不绑定 `self`**（私有名字被字面保留，不做名称改写）⇒ 挂类才能按类属性绑定。

## 已知边界

- 判定依赖「媒体库目录」已配置；未配置时 `__get_library_index` 返回空 ⇒ **自动不生效、不报错**。
- 订阅（RSS）链路不经过本判断，本版只覆盖**刷流选种**。
- 剧集按「发布名 = 库名」匹配；若发布方与库的片名写法差异过大仍会漏判（漏判 = 放行，不误拦）。
- 季号对齐对「库里没有该季」的种子会查一次 TMDB（有网络依赖，失败则放行）。

# v6.4.4 — 缺集只统计「已播出」的集（只改展示口径，判定与搜索一字不动）

## 背景

用户在订阅详情弹窗看到「第 4 季 · 缺失剧集（共 26 集）：第 1 集 … 第 26 集」，提出
「诛仙第四季还没到 26 集，应该只缺到最新上映的吧」—— **用户是对的**。

事实取证（三条独立来源 + 代码）：

| 来源 | 内容 |
|---|---|
| 公开资料（tnt43 / icydmw） | 诛仙第四季（别名「诛仙 最终季」）**更新至第 09 集**、标签「连载」，2026-10-02 更新到第 9 集 |
| TMDB / NeoDB / Eggplant | 该季 **number of episodes = 26**（即 TMDB `seasons[].episode_count`） |
| 代码 | 总集数唯一来源 `Media.get_tmdb_season_episodes_num()` 直接读 `seasons[].episode_count`，**全仓没有「已播出」概念** |

⇒ **26 是「宣布的总集数（含未播出）」，已播出只有 9**。

## 链路（为什么界面是 26）

```
subscribe.py:863  check_exists_medias(meta_info, total_ep={4: 26})
  └─ downloader.py:1660  episode_num = 26
       └─ get_no_exists_episodes(meta_info, 4, 26)      # 库里 0 集 ⇒ 缺 1..26
            └─ downloader.py:1704  len(26) >= 26 ⇒ no_item={"episodes": [], "total_episodes": 26}
                 └─ subscribe.py:1014  用 1..total_episodes 填充
                      └─ RSS_TV_EPISODES（明细）+ RSS_TVS.LACK = 26
                           └─ functions.js:589  「共 26 集：第 1 集 … 第 26 集」
```

## ★ 为什么不能顺手把判定基数也改成 9

若把传给 `check_exists_medias` 的 `episode_count` 也换成已播出数：
库里 1..9 齐 ⇒ 缺集为空 ⇒ `downloader.py:1752` 判 `return_flag=True`（已全部存在）
⇒ `subscribe.py:874-882` 走 `finish_rss_subscribe()` ⇒ **本季还在连载却中途自动退订**，
后续新集再也不下。现状之所以没踩到，是**靠那个还没播出的第 26 集当「哨兵」**——巧而非设计。

⇒ 本版**只改展示口径**：`check_exists_medias` 的 `return_flag` / `no_exists`
（= 退订判定与搜索目标）与 `Torrent.get_intersection_episodes` 一字不动；
`RSS_TVS.LACK`（卡片进度条分子）仍按**全量**计，卡片继续如实显示「已入库 9 / 共 26」。

## 改动（5 文件 / 8 处）

| 文件 | 位置 | 改动 |
|---|---|---|
| `app/media/media.py` | 新增 `get_tmdb_season_aired_episodes_num()` | 由 `last_episode_to_air` 推算已播出集数：目标季 < 当前播出季 ⇒ 该季总集数（已播完）；== ⇒ 其集号；> ⇒ 0；拿不到 ⇒ 0（**不裁剪**） |
| `app/helper/db_helper.py` | `update_rss_tv_lack()` | 新增可选 `episodes_display`：`LACK` 仍按 `lack_episodes` 全量计，`RSS_TV_EPISODES` 写 `episodes_display`；不传时与旧语义**完全一致** |
| `app/subscribe.py` | `update_subscribe_tv_lack()` | 算出 aired 后把落库明细裁剪为只含已播出的集；`aired <= 0` 不裁剪 |
| `app/subscribe.py` | `refresh_rss_metainfo()` | 同一口径：写回登记簿前同样按 aired 裁剪 |
| `web/action.py` | `__media_info` 弹窗接口 | 新增 `aired_episodes` / `total_episodes`（仅在季号 > 0 时查一次 TMDB，非订阅弹窗不发请求） |
| `web/static/js/functions.js` | 缺集区块 | 追加播出进度；明细为空但有进度时显示「已播出的集已全部入库」 |

## 验证

| 层 | 内容 | 结果 |
|---|---|---|
| 离线（行为） | `_verify_644.py`：`Media` 两个方法、`DbHelper.update_rss_tv_lack`、`Subscribe.update_subscribe_tv_lack` **全部由真实源码 AST 派生后 exec** | **51/51** |
| 关键用例 | 诛仙形态（总 26 / 播到 9） | 库 0 集 ⇒ LACK=26、明细=1..9；库 1..9 ⇒ 明细清空、LACK=17；库 1..5 ⇒ 明细=6..9、LACK=21 |
| 边界 | 已播完的季 / 拿不到 `last_episode_to_air`（aired=0）/ 季号不匹配 | 均**不裁剪**，与 v6.4.3 一致 |
| 反向对照 | 用备份里的旧 `update_subscribe_tv_lack` 驱动同一输入 | 得到明细 1..26 且无 `episodes_display` ⇒ 证明确实生效 |
| 逐字节可逆 | 用补丁常量反向替换后与备份**逐字节相同**（5 文件全覆盖） | ✓ |
| 回归守卫 | `git diff` 不含 `downloader.py` / `searcher.py` / `media_server.py` / 两个客户端；`subscribe.py` 里 `check_exists_medias` 调用段、退订分支、`get_intersection_episodes` 段**逐字节未变** | ✓ |
| 回归 | `_verify_643.py` 54/54 ｜ `_verify_642.py` 57/57 ｜ `_verify_641_lack_fallback.py` 35/35 ｜ `_verify_640_subscribe.py` 55/55 ｜ `_verify_log_api_usage.py` 7/7 | 全绿 |

## 踩坑

- `_verify_640_subscribe.py` 的 `make_obj()` **没有 `self.media`** ⇒ 新代码调
  `self.media.get_tmdb_season_aired_episodes_num()` 抛 `AttributeError`，被
  `subscribe_search_tv` 的 `except Exception` 吞掉 ⇒ 表现为「静默不落库」+ 后续
  `search_args[0]` IndexError。这是**桩缺属性**（生产侧 `Subscribe.init_config()` 里
  `self.media = Media()` 一直存在），已按纪律**从真实源码派生**补桩，而不是手写。
- **抽取方法时装饰器会丢**：真实 `Media.get_tmdb_season_*` 带 `@staticmethod`，
  直接拼进桩类会退化成实例方法 ⇒ `self.media.f(tv_info=..., season=...)` 会把实例
  错位绑定到 `tv_info`。已显式补回 `staticmethod`。
- 不要写成 `for n in ("a", "b"): setattr(...)` —— `_verify_log_api_usage.py` 的 [B] 段
  启发式会把这种形状（含其后 6 行内的 `setattr(`）当成「log 桩名字表」而误报，
  已改为两条显式 `setattr`。

## 已知边界

- **未开播的季**（aired == 0）不裁剪 ⇒ 界面仍会把整季列为「缺失（待扫描确认）」。
  闭合需要额外区分「未开播」与「取不到播出信息」，本版未做。
- `get_tv_episodes` 内部把「季列表为空 / 查询抛异常」吞成 `[]`（v6.4.3 已记录的边界），本次未改。
- `RSS_TVS.TOTAL` 仍是 TMDB 总集数；TMDB 后续改集数时由 `refresh_rss_metainfo()`
  （受 `name_follow_tmdb_changed` 开关控制）同步，与本版改动无关。
- 存量订阅的登记簿要等**下一轮订阅扫描**才会按新口径刷新，界面在此之前仍显示旧明细。

# v6.4.3 — 飞牛/绿联「查不到剧 / 查不了」时返回 None（不再误报缺集、不再误退订）

## 背景（承接 v6.4.2 的追问）

v6.4.2 修的是绿联「季名两种语序」导致订阅误报缺集。随后复核**飞牛影视有没有同样的问题**，
结论是「一半有、一半没有」：

| | 绿联 | 飞牛 |
|---|---|---|
| A 类**触发原因** | 季名 `季 N` 语序解析失败 | 剧名 / 年份**精确比对**不中 |
| A 类**症状**（误报整季缺 + 重复下载） | 有 | **同样有** |
| B 类（返回 `[]` 被当「已完整」⇒ 误退订） | v6.4.2 已修 | **未修** |

## 根因与契约

`check_exists_medias()`（`app/downloader/downloader.py:1687-1692`）的既定契约是：

```python
no_exists_episodes = self.mediaserver.get_no_exists_episodes(...)
if no_exists_episodes is None:      # None = 无法确认 ⇒ 回退本地目录扫描
    no_exists_episodes = self.filetransfer.get_no_exists_medias(...)
if no_exists_episodes:              # 非空 = 缺这些集
    ...
# 客户端与目录扫描都拿不到东西 ⇒ return_flag 保持 False ⇒ 在 1752 行被置 True（= 已全部存在）
```

各客户端在这一层的失败返回值**必须**是 `None`：

| 客户端 | `get_no_exists_episodes` 失败 | `get_movies` 失败 |
|---|---|---|
| emby | `None` | `None` |
| jellyfin | `None` | `None` |
| plex | `None` | `None` |
| ugreen | `None`（v6.4.2 修） | **`[]`** ← 本版修 |
| trimemedia | **`[]`** ← 本版修 | **`[]`** ← 本版修 |

而 `[]` 在调用方眼里是「**一集都不缺**」，于是：
连接失败 / 查询异常 ⇒ 订阅被判 `exist_flag=True` 且 `library_no_exists` 为空 ⇒
`app/subscribe.py:869` 走 `finish_rss_subscribe()` ⇒ **订阅被静默判完成并清除**。

飞牛还有第二条同源路径：`__get_series_id_by_name()` 用「剧名精确相等 + 年份精确相等」找剧，
找不到就返回 `None` ⇒ `get_tv_episodes()` 返回 `[]` ⇒ `get_no_exists_episodes()` 把
`exists_nums` 算成空 ⇒ **返回全部集号**（= 误报「整季全缺」）⇒ 反复提交下载。
症状与绿联 A 类一致，只是触发原因不同（绿联是季名语序，飞牛是剧名/年份比对）。

## 改动（2 文件 / 7 处，全部是「查不到时返回 None」）

| 文件 | 位置 | 改动 |
|---|---|---|
| `app/mediaserver/client/trimemedia.py` | `get_movies` 未就绪 / 异常 | `return []` → `return None` |
| 同上 | `get_no_exists_episodes` 未就绪 | `return []` → `return None` |
| 同上 | `get_no_exists_episodes` **新增预检** | 按名字找不到剧 ⇒ 记日志并 `return None` |
| 同上 | `get_no_exists_episodes` 类型防御 / 异常 | `return []` → `return None` |
| `app/mediaserver/client/ugreen.py` | `get_movies` 未启用 / 异常 | `return []` → `return None` |

**刻意不动 `get_tv_episodes`**：它同时被「媒体库同步」使用（`app/mediaserver/media_server.py:325`
存 `seasoninfo`），改它的返回契约会波及同步数据 ⇒ 把「按名字找不到剧」的判定放进
`get_no_exists_episodes` 里做预检，公开契约保持原样。

**正常路径行为完全不变**：库里真的没有这一季 ⇒ 仍返回全部集号（正确答案）；
库里已齐 ⇒ 仍返回 `[]`（正确答案）。只有「查不了」才变成 `None`。

## 验证

| 层 | 内容 | 结果 |
|---|---|---|
| 离线（行为） | `_verify_643.py`：飞牛 / 绿联方法**由真实源码 AST 派生**后 exec，跑失败路径 + 正常路径真值表 | **54/54** |
| 端到端 | 真实 `check_exists_medias` + 同一磁盘真相（缺 1/2/3）对照 `None` vs `[]` | 见下 |
| 端到端结果 | 客户端返回 `None` ⇒ 回退目录扫描被调用、`exist_flag=False`、缺失集落库；<br>客户端返回 `[]`（旧） ⇒ **不回退、`exist_flag=True`（磁盘明明缺 3 集）** | **符合预期** |
| 反向对照 | PREV（`19578d6`）旧实现：找不到剧 ⇒ **返回全部集号**（误报整季缺）；未就绪 ⇒ **返回 `[]`**（误判已完整） | 已钉死 |
| 契约护栏 | 脚本内断言 `get_tv_episodes` 与 PREV **逐字节相同** | ✓ |
| 回归 | `_verify_642.py` 57/57 ｜ `_verify_641_lack_fallback.py` 35/35 ｜ `_verify_640_subscribe.py` 53/53 | 全绿 |
| 桩合规 | `_verify_log_api_usage.py` 7/7（桩名全部从 `log.py` 真实公开面派生） | ✓ |

桩纪律：假 `_TrimeApi` 四个方法的**形参名**、假 `MediaServer` / `FileTransfer` / `Media`
的方法名与形参名，全部由真实源码 AST 派生并在脚本中断言；`meta_info` 用到的属性
由正则从被测方法源码里抽出后断言 —— 避免「桩比产品更宽容」造成的假绿。

## 踩坑

- `attrs_on()` 用 `ast.parse()` 解析**类体缩进片段**会 `IndentationError` ⇒ 必须先 `textwrap.dedent()`。
- 断言辅助 `check(tag, cond, extra)` 必须容忍 `extra=None`，否则**断言失败的打印本身再抛
  `TypeError`**，把真正的失败信息整个盖掉。
- 复用旧版验证脚本时，「未就绪」场景要用 `_api=None`，而不是一个 token 齐全的假 `_TrimeApi` ——
  否则测到的是「找不到剧」分支，**两条分支会看起来都通过**。
- `_verify_640_subscribe.py` / `_verify_641_lack_fallback.py` 里各有一条断言**自 v6.4.2 起
  必然为假**（它们要求订阅卡片含 `Attr.lack_episodes*`，而 v6.4.2 按用户要求把该区块整段删了）。
  这是**断言过时不是回归**（已用 git 核对：v6.4.1 有 6 处、v6.4.2 起 0 处），
  已就地改成新语义 —— 断言卡片**不再**含缺集区块。

## 已知边界

- `get_tv_episodes` 内部把「季列表为空 / 查询抛异常」一律吞成 `[]`，而 `[]` 在
  `get_no_exists_episodes` 里等于「该季一集都没有」⇒ 这类**瞬时故障**仍会表现为
  「整季全缺」（**响亮失败**：会重复搜索下载，但**不会**静默误删订阅）。
  彻底闭合需要让 `get_tv_episodes` 区分「查不到」与「确实没有」，属公开契约变更，本版未做。
- 本机媒体服务器是**绿联**，无飞牛真机环境 ⇒ 上述结论来自**代码 + 离线**验证，未做飞牛真机复验。
- 绿联 `get_tv_episodes` 自身异常时同样仍返回 `[]`（同上，未改）。

# v6.4.2 — 绿联「季 N」条目名导致订阅缺集误报 + 缺集明细标明季

## 现象（真机取证，2026-10-04）

用户在订阅页看到：诛仙 S04 卡片「正在订阅 (0/26)」+「缺 26 集」；点开详情写
「缺失剧集（共 26 集）：第 1 集 … 第 26 集」；而绿联影视里诛仙看着并不缺。
**注意本机媒体服务器是绿联（`UgreenClient`），不是飞牛。**

## 取证链（全部来自 `nas-tool` 容器内只读调用）

| 层 | 手段 | 结果 |
|---|---|---|
| 订阅表 | `RSSTVS` | id1~4 = 诛仙 **S04/S03/S02/S01**（tmdb 206484，TOTAL=26，LACK=26）；id5 = 凡人修仙传 S01（tmdb 106449，TOTAL=206，LACK=206） |
| 缺集登记簿 | `RSS_TV_EPISODES` | id1~4 = `1..26`；id5 = 无记录 |
| 绿联条目 | `_all_library_videos()` | 956 条（type=2 剧集 **123** 条）；诛仙的条目名是 **`诛仙 季 1` / `诛仙 季 2` / `诛仙 季 3`**，**没有「季 4」** |
| 绿联集列表 | `getTV(131/124/121)` | 季1 = 27 集（1–26 + **47**）缺 0；季2 = 26 缺 0；季3 = 26 缺 0 |
| 绿联集列表 | `getTV(185)` | 凡人修仙传季1 = 191 集，**真缺 15 集**（183、190、194–206） |
| 客户端 | `_find_tv_candidates("诛仙")` | **`[]`** ⇒ `check_exists_medias` 判「第 4/3/2/1 季 缺失 26 集」 |

## 根因

`ugreen.py` 的 `_normalize_title` / `_parse_season_num` **只认 `第 N 季` 与 `Season N`**。
而绿联库里「季」后缀有**两种语序并存**，实测 123 条剧集中：

- `第 N 季`（含「第 1 季」无空格）—— **58 条**，旧实现能认；
- **`季 N`（数字在「季」之后，如「诛仙 季 1」「生活大爆炸 季 12」）—— 47 条，旧实现两条正则都不命中**；
- 另有中文数字形态（`大道朝天 第一季`）。

于是 `诛仙 季 1` 归一化后仍是 `诛仙 季 1` ≠ `诛仙` ⇒ `_title_matches` 恒 False ⇒
`_find_tv_candidates` 恒空 ⇒ 客户端认为「媒体库里没有这部剧」⇒
**该剧每一季都被判为「全 26 集都缺」** ⇒ 界面误报 + 反复提交下载。

顺带发现一个**更危险**的同源缺陷：`UgreenClient.get_no_exists_episodes` 在
`not self._api`（登录失败时 `init_config` 会把 `_api` 置 None，见 `ugreen.py:700/717`）
与自身异常时 `return []`；而 `check_exists_medias` 把 `[]` 当作「**一集都不缺**」⇒
`return_flag=True` ⇒ **订阅被当成已完成并自动清除**。
emby/plex 在 `get_no_exists_episodes` 这一层返回的都是 `None`（= 无法确认，由调用方回退
`filetransfer.get_no_exists_medias` 做目录扫描）—— 绿联独树一帜，本版对齐。

## 改动

| 文件 | 改动 |
|---|---|
| `app/mediaserver/client/ugreen.py` | ① `_normalize_title` 增补 `季 N` 与中文数字季的剥离 ② 新增 `_cn_numeral`，`_parse_season_num` 增补 `季 N` / 中文数字 ③ `get_no_exists_episodes` 三处 `return []` → `return None` |
| `web/templates/rss/tv_rss.html` | 删除订阅卡片的缺集区块（**纯删除 12 行、0 插入**） |
| `web/action.py` | `__media_info` 初始化并透传 `rss_season`（订阅的季，形如 `S04`） |
| `web/static/js/functions.js` | 详情弹窗缺集明细改「第 N 季 · 缺失剧集（共 M 集）：…」，标题行追加「· 第 N 季」；`S00`/无该字段时不加季 |

**不新增数据库字段、不新增迁移、不动搜索与下载逻辑、不动「补齐后自动退订」的判定。**

前端季号解析用 `String(ret.rss_season).match(/(\d+)/)` 容错：拿到 0（`S00`）或字段缺失时
一律不加季前缀，保证老报文与「整剧订阅」行为不变。

## 验证

| 层 | 内容 | 结果 |
|---|---|---|
| 离线（客户端） | `_verify_642.py`：三个静态方法**由真实源码 AST 派生**后跑真值表（29 条真实库条目名）+ 失败路径 AST 判据 + 4 文件的字节判据 | **57/57** |
| 离线（前端） | `_verify_642_frontend.py`：Jinja2 真渲染 tv_rss.html + node 桩驱动 `show_mediainfo_modal` 6 分支 | **24/24** |
| 反向对照 | 归一化/匹配函数在旧实现下 `诛仙 季 1` 必为 False（见根因） | 已钉死 |
| **真机** | 部署 4 文件到容器（md5 逐一核对）+ 重启后复跑 | 见下 |
| 真机结果 | `_find_tv_candidates("剑来")` → 2 条；诛仙 S01/S02/S03 → `exist_flag=True 缺集=[]`（**下一轮扫描将自动完成订阅**）；S04 → 缺 26（**真实存在，绿联确实没有第 4 季**）；凡人 S01 → 缺 15（183、190、194–206） | **符合预期** |
| 接口层 | 容器内调 `WebAction.__media_info`：`rss_season='S04' / 'S01'` | ✓ |
| 服务 | 容器重启后 Web `3000 = 200`，启动日志无异常 | ✓ |

## 踩坑

- `ast.get_source_segment` 对**带装饰器**的静态方法会**丢掉 `@staticmethod`、首行丢缩进** ⇒
  离线派生桩必须按 `decorator_list[0].lineno .. end_lineno` **按行取源码**，否则方法退化成实例方法。
- `tv_rss.html` 进度条用的是上游原写法 `(Attr.total or -1) - (Attr.lack or -1)`：`lack=0` 时
  0 被 `or` 吞成 -1 ⇒ 显示 `13/12`。**本版未动这一行**（既有小瑕疵，非本次范围），
  验证脚本按实际行为断言，不按「应该是 12/12」断言。

## 已知边界

- 存量订阅的 `RSS_TV_EPISODES` 仍是修复前的旧值 ⇒ 界面数字要等**下一轮订阅扫描**
  （`search_rss_interval`，本机配置 6 小时）才刷新；届时诛仙 S01~S03 会因「已完整」被自动退订，
  凡人修仙传会被改写成真实的 15 集。
- 绿联 `get_tv_episodes` 自身异常时仍返回 `[]`（本版未改，它对「媒体库同步」也有调用方），
  因此连接异常在那一层仍表现为「全集缺失」—— 属**响亮失败**（会重复下载），不会静默误删订阅。
- 中文数字季仅覆盖 一 ~ 九十九；其他写法（罗马数字等）未覆盖。

# v6.4.1 — 升级前建的订阅：缺集明细为空时按「整季」兜底

## 现象（真机取证，2026-10-04）

在 NAS `nas-tool` 容器内只读查询 `user.db`：

| ID | 名称 | 季 | TOTAL | LACK | TOTAL_EP | CURRENT_EP | RSS_TV_EPISODES |
|---|---|---|---|---|---|---|---|
| 1 | 诛仙 | S04 | 26 | 26 | NULL | NULL | `1..26` |
| 2 | 诛仙 | S03 | 26 | 26 | NULL | NULL | 无 |
| 3 | 诛仙 | S02 | 26 | 26 | NULL | NULL | 无 |
| 4 | 诛仙 | S01 | 26 | 26 | NULL | NULL | 无 |
| 5 | 凡人修仙传 | S01 | 206 | 206 | NULL | NULL | 无 |

在容器内调用 `Subscribe().get_subscribe_tvs()`（v6.4.0 代码）：5 条订阅都返回了
`lack_episodes` 字段，但只有 id=1 有值（26 集），**其余 4 条是空列表** ——
于是界面出现「卡片显示缺 26 集、点开弹窗列不出集号」。

## 根因

`RSS_TV_EPISODES` 是缺集明细的落库载体（`RSSTVS.LACK` 只存数量），
而它**只在订阅扫描走到「本轮没搜到 / 仍缺」的分支时才被写**。
v6.4.0 之前创建的订阅未被新逻辑扫过，因此只有数量、没有明细。

## 判定口径（为什么可以确定地补出来）

- `TOTAL_EP` / `CURRENT_EP` 为 NULL ⇒ 订阅范围就是全集；
- `LACK == TOTAL` ⇒ 全部缺失。

两者同时成立时，缺的集必然是 `1..TOTAL`。
反之若 `0 < LACK < TOTAL` 而明细缺失，则**无法知道缺哪些集** ⇒ 保持空、不臆造，
界面只显示数量，等下一轮扫描（`search_rss_interval`，本机配置 6 小时）写入真实明细。

## 改动

| 文件 | 改动 |
|---|---|
| `app/subscribe.py::get_subscribe_tvs` | 明细为空且 `LACK >= TOTAL > 0` 时按 `1..TOTAL` 兜底；新增输出 `lack_episodes_estimated` |
| `web/action.py::__media_info` | 透传 `lack_episodes_estimated` |
| `web/templates/rss/tv_rss.html` | 卡片摘要加「（整季）」与「待扫描确认」 |
| `web/static/js/functions.js` | 弹窗文案加「整季缺、待扫描确认」 |

**不新增数据库字段、不新增迁移、不改搜索与下载逻辑。**
数值读取做了 `int() + try/except` 保护：`TOTAL/LACK` 为 NULL 或脏字符串时不抛异常，
安全退化为空明细。

## 验证

| 层 | 内容 | 结果 |
|---|---|---|
| 静态 AST | 兜底形态、脏数据保护、输出字段、界面/JS 标注、旧分支消失 | 10/10 |
| 桩签名 | 7 个桩从真实模块 AST 派生逐参数比对 | 8/8 |
| **动态** | 真实 `get_subscribe_tvs` / `get_subscribe_tv_episodes` / `__parse_rss_desc` 源码 exec 进宿主类，11 组场景 | 11/11 |
| **反向对照** | 旧实现（钉死 `56d4734`）在真机形态下明细必须为空 | 3/3 |
| **真机数据复刻** | 用 NAS 取回的 5 行真实字段值复跑 | 3/3 |
| 前端 | Jinja2 真渲染 12 项 + 弹窗区块 3 项 + Node 驱动 JS 8 项 | 23/23 |
| 回归 | v6.4.0 订阅验证 53/53、蜜柑 34/34、A1 抓取分类 36/36、log API 7/7、全量健康体检 | 全通过 |

场景要点：登记簿有明细走登记簿；无明细 + 整季缺走兜底（`1..TOTAL`、标记 estimated）；
部分缺且无明细保持空；登记簿空列表同样兜底；NULL 全空不崩；脏字符串不崩；
登记簿乱序输出升序；`LACK > TOTAL` 不越界；多订阅互不串。

## 已知边界

- 兜底值是**推算值**（`1..TOTAL`），界面明确标注「待扫描确认」；
  下一次自动扫描写入真实集号后标注自动消失。
- `0 < LACK < TOTAL` 且无明细时仍列不出集号 —— 属信息本身缺失，不做臆造。

# v6.4.0 — 订阅：缺集可见 + 「补齐才退订」的完成判定重写

## 需求

1. 电视剧订阅后要能**看到缺的是哪几集**（原来只有进度条与 `(已/总)`，没有集号）；
2. 订阅**补齐之后自动清除**（用户拍板口径：**等真的补齐才退订**，即媒体库查得到才算）。

## 改前的链路（`Subscribe.subscribe_search_tv`）

电视剧订阅有三条「完成订阅」出口，全部指向 `finish_rss_subscribe()` → `delete_subscribe()`：

| 出口 | 触发条件 | 判读 |
|---|---|---|
| a | `check_exists_medias()` 返回 `exist_flag=True` 且无缺集 | ✅ 真的补齐了 |
| b | `search_one_media()` 返回的 `search_result` 非空 | ⚠️ **只代表种子已提交给下载器** |
| c | 缺集交集为空 / `no_exists` 无该 tmdb_id | ✅ 本轮无可做 |

问题就在 **b**：`Searcher.search_one_media()` 的返回语义是

```
全部下完  -> return download_items[0], no_exists, ...
有剩余缺失 -> return None, left_medias, ...
```

`search_result` 非空 = 「本批需要的集**都提交下载了**」，**不等于已入库**。
一旦用户片单的种子下载失败（死种、磁盘满、下载器离线），订阅已被删，
这一集就永远缺着，且没有任何提示。

## 关键取证（都来自真实源码）

- **`left_medias` 是就地修改的字典**：`Downloader.batch_download()` 的
  `__update_episodes()` 会把**已提交下载的集从 `need_tvs` 里移除**，
  所以它精确表示「本次没提交成功的集」。
- **`check_exists_medias()` 的三态**：
  `True` = 全部存在 / `False` = 有缺失 / **`None` = 无法查询**（拿不到总季数或查不到 TMDB 详情）。
  原版 `if exist_flag:` 对 `None` 不成立，会一路走到 b 或 c ⇒ **媒体库不可用时可能被误清**。
- **`library_no_exists` 就是媒体库当前真实缺集**，且「整季都缺」时是
  `{"season": N, "episodes": [], "total_episodes": M}`（**空列表**表示整季，不是「没有缺失」）。
- **`Torrent.get_intersection_episodes()` 对空 episodes 的行为**：
  `source_info["episodes"]` 为空时直接 `continue`（不裁剪），
  `target_info["episodes"]` 为空时取 source —— 即「缺集为空列表 = 整季」能被正确保留。
  另外它的交集结果由 `list(set(...))` 产生 ⇒ **顺序不稳定**，展示前必须排序。
- **`RSS_TV_EPISODES.EPISODES`** 就是缺集明细的落库载体（`RSSTVS.LACK` 存数量），
  原来只在「本轮没搜到」的分支里被写；**数据本来就有，缺的只是出口**。
- **扫描周期**：已订阅（state=`R`）由 `subscribe_search_all` 按
  `pt.search_rss_interval` 小时扫描，代码里**下限 6 小时**；
  新订阅（state=`D`）由 `RSS_CHECK_INTERVAL=300` 秒处理。⇒ 「保持订阅等入库」的实际重试
  间隔是 ≥6h，不会打爆站点。

## 改动

### A. `app/subscribe.py`

1. **显式的「订阅范围」**：新增 `subscribe_episodes = range(current_ep, total_ep+1)`
   （无 `current_ep` 则 `range(1, total_ep+1)`），作为搜索目标的上限；
   登记簿为空时退化为它。
2. **`exist_flag is None` ⇒ 跳过本轮**：打日志 + `update_rss_tv_state(state='R')` + `continue`。
   不退订、不搜索。
3. **每轮用媒体库缺集刷新登记簿**：
   `update_subscribe_tv_lack(seasoninfo=library_no_exists.get(tmdb_id) or [])`
   ⇒ `LACK` 与 `EPISODES` 从此等于「媒体库当前真实缺集」，界面展示与用户感知一致。
4. **搜索目标 = 订阅范围 ∩ 媒体库实际缺集**：
   `Torrent.get_intersection_episodes(target={tmdb_id:[{season, episodes: subscribe_episodes, total_episodes}]}, source=library_no_exists, ...)`
   ⇒ 已经入库的集**不会再被提交**（原版靠登记簿「只减不增」防重，现在靠媒体库实际状态，
   下载失败也能在下一轮自然重试）。
5. **删除出口 b**：`search_result` 非空时不再 `finish_rss_subscribe`，只打一行
   「已提交下载，等待入库后自动完成订阅」，然后**恢复 `state='R'`**。
   完成订阅从此**只有** `exist_flag=True` 一条路。
6. **`update_subscribe_tv_lack()` 支持整季缺**：`episodes` 为空列表时用
   `list(range(1, total_episodes+1))` 填充，保证 `LACK` 与明细都准确。
7. **洗版分支收窄**：原写法 `if search_result or not no_exists or ...` 后无条件调
   `update_subscribe_over_edition(media=search_result)`，`search_result=None` 时会在
   `media.res_order` 上抛 `AttributeError`（被外层 `except` 吞成「订阅搜索失败」）。
   现在只在 `search_result` 非空时调用，否则复位 `state='R'`。
8. **`get_subscribe_tvs()` 输出 `lack_episodes`**（排序后的缺集明细）。

### B. `app/helper/db_helper.py`

`get_rss_tv_episodes()` 过滤空串：`[int(epi) for epi in str(...).split(',') if str(epi).strip()]`。
历史数据里可能残留 `EPISODES=""`（清空缺失集后又写入），原写法 `int('')` 会抛 `ValueError`。

### C. 界面

| 文件 | 改动 |
|---|---|
| `web/templates/rss/tv_rss.html` | 卡片状态行下新增缺集摘要「缺 N 集：第 x、y、z」（>12 集截断 + `title` 放全量）；顺修一处 `class=“...”` 全角引号 |
| `web/action.py` `__media_info` | 订阅分支返回 `lack_episodes`（直接复用 `get_subscribe_tvs` 已算好的值，零额外查询） |
| `web/templates/navigation.html` | 媒体详情弹窗新增 `#system_media_lack_info` 区块（默认 `display:none`） |
| `web/static/js/functions.js` | `show_mediainfo_modal` 渲染缺集；**无缺集/老报文无该字段时显式 `hide()`**（避免上一次弹窗残留） |

**未新增任何数据库字段、未新增迁移、未新增配置项。**

## 验证

### 离线（真实源码驱动，不手抄）

| 层 | 内容 | 结果 |
|---|---|---|
| 静态 AST | 旧分支消失、`finish_rss_subscribe` 仅剩 1 处且在 `exist_flag` 分支内、三态处理、整季填充、`lack_episodes` 输出 | 9/9 |
| **动态** | 把**真实** `subscribe_search_tv` 等 5 个方法的源码按 AST 抽出来 exec 进宿主类，配真实 `Torrent.get_intersection_episodes` 与真实 `MetaBase.get_season_seq`，跑 6 组场景 | 20/20 |
| **反向对照** | 同一场景喂给旧实现（`git show 0a5ce8a:app/subscribe.py`）⇒ **必须退订**（证明修复有牙） | 2/2 |
| 桩合规 | 7 个桩的签名与真实模块 AST 逐参数比对；`log` 桩名字必须落在 `log.py` 真实公开面内（无 `warning`） | 9/9 |
| 前端 | Jinja2 真渲染卡片（有缺集/无缺集/老数据无键/超 12 集截断/整季缺/进度条回归）；Node 桩驱动 `show_mediainfo_modal` 三分支 | 18/18 |
| 健康 | 全量 `py_compile` + 装饰器同行粘连 AST + 路由装饰器真实 eval | 通过 |

场景要点：

1. 媒体库已全部存在 ⇒ 退订、不搜索；
2. 媒体库缺 `[3,5]`、本轮提交成功 ⇒ **不退订**、登记簿写 `[3,5]`、搜索目标 `[3,5]`、状态复位 `R`；
3. `current_ep=3` 且媒体库缺 `[1,3,5]` ⇒ 搜索目标 `[3,5]`（第 1 集在订阅范围外），
   而**展示的缺集仍是 `[1,3,5]`**（用户要看媒体库真缺什么）；
4. `exist_flag=None` ⇒ 不退订、不搜索、状态复位；
5. 洗版 + 本轮无结果 ⇒ 不抛异常、不退订；
6. 整季缺（`episodes: []`）⇒ 登记簿 `1..12`、搜索目标整季；
7. 登记簿有历史值 `[9,10]` ⇒ **不会把目标收窄**（目标只由媒体库决定）。

### 既有回归

蜜柑 34/34、A1 抓取状态分类、`log` API 合规守卫 7/7、全量 `py_compile` 与装饰器体检 —— 全部通过。

## 已知边界

- 「补齐」的口径**依赖媒体服务器或媒体库目录**：两者都不可用时 `check_exists_medias()`
  可能得到「全部存在」的结论并退订 —— 这是**改动前就有的行为**，本次未改变；
  本次只把「无法查询」（`None`）这一种情形单独拎出来保护。
- 未下载完成的集会在下一个扫描周期（默认 ≥6 小时）被**重新搜一次**，这是刻意的失败自愈；
  同一批种子的重复提交由下载器按种子特征去重兜底。
- 「界面上显示缺集」与「订阅是否完成」共用同一个数据源（媒体库缺集），
  所以建议把媒体库（Emby/Jellyfin/Plex/飞牛/绿联）或同步目录配好，否则缺集只能退化为按目录扫描。

# v6.3.8 — 蜜柑「一条都搜不到」：站点改版 + 归因误判（两个成因叠加）

## 现象

用片名搜索番剧时，**蜜柑（mikanime.tv）**返回 0 条，日志里出现两行：

```
蜜柑 未解析到种子：页面包含登录表单（name="username"），Cookie 可能已失效
蜜柑 抓取未成功[needLogin]：页面包含登录表单（name="username"），Cookie 可能已失效
```

而蜜柑是**公开站**，站点配置里根本没有 Cookie ⇒ 「Cookie 失效」这个结论本身就不可能成立。
这正是本次要修的重点：**症状（报登录）与病因（站点改版）完全对不上**。

## 根因（真机取证，两个成因缺一不可）

### R12 · 站点改版 ⇒ 行选择器命中 0 条（主因）

蜜柑搜索页的 DOM 变了。真机实测（NAS 生产容器直连，700490 B 页面 / 168 个 `<tr>`）：

| 变化点 | 改版前 | 改版后（实测） |
|---|---|---|
| `div.central-container` 与 `table` 之间 | 直接子元素 | **新增一层 `div.episode-table`** |
| 结果行 | `tr.js-search-results-row` | 同名，但层级深了一层 |
| 每行首列 | 标题 + 磁链 | **新增勾选框列**（后续列序号整体 +1） |
| 行内列序 | td1 标题 / td2 体积 / td3 时间 | **td1 勾选框 / td2 标题 / td3 体积 / td4 时间** |

原规则是：
`div.central-container > table > tbody > tr.js-search-results-row`
—— `>` 要求**直接子元素**，多一层就命中 0 ⇒ 一条都解析不出来。

### R13 · 归因误判 ⇒ 把「改版」报成「Cookie 失效」

`classify_page_state()` 第 5 条规则是「页面里出现登录表单元素（`name="username"` 等）
就判 `needLogin`」。这条规则**特异度太低**：

- 蜜柑的**页头与页脚**各有一个「可选登录」浮层
  （`action="/Account/Login?ReturnUrl=..."`，位置约在页面的 0.8% 与 99.3% 处）；
- 字段名是 `name="UserName"`，**小写化后正好命中** `name="username"`。

于是**任何一页**（包括正常的搜索结果页）都会被判成登录页 ——
「站点改版」这一真因被彻底掩盖，排查方向被带偏到「去更新 Cookie」。

## 改动

### A. `web/backend/user.sites.bin`（mikanani 条目，恰 6 处选择器）

| 路径 | 旧 | 新 |
|---|---|---|
| `torrents.list.selector` | `div.central-container > table > tbody > tr.js-search-results-row` | `div.central-container table > tbody > tr.js-search-results-row` |
| `torrents.fields.title.selector` | `td:nth-child(1) > a.magnet-link-wrap` | `a.magnet-link-wrap` |
| `torrents.fields.details.selector` | `td:nth-child(1) > a.magnet-link-wrap` | `a.magnet-link-wrap` |
| `torrents.fields.download.selector` | `td:nth-child(1) > a.js-magnet.magnet-link` | `a.js-magnet.magnet-link` |
| `torrents.fields.size.selector` | `td:nth-child(2)` | `td:nth-child(3)` |
| `torrents.fields.date_added.selector` | `td:nth-child(3)` | `td:nth-child(4)` |

选择策略改成**按类名定位**（`a.magnet-link-wrap` / `a.js-magnet.magnet-link`）
而不是按列序号 —— 这样以后再插入/删除列也不会失效。

> `user.sites.bin` 是 base64（无换行）包裹的紧凑 JSON（`separators=(",", ":")`，
> `ensure_ascii=False`），顶层键 `version` / `indexer` / `conf`，共 111 条站。
> 本次改动经**递归结构化 diff** 断言：仅 mikanani 这 6 处变化，
> `version` 段、条目总数、`conf` 段**字节不变**；未改动的 dict 空跑一次
> 序列化后与原字节**完全一致**（证明格式保真）。501924 B → 501848 B（−76 B）。

### B. `app/indexer/client/_spider.py`（4 处）

1. **新增 `_has_result_listing(html_text)`**：
   `html_text.lower().count("<tr") >= 5` ⇒ 页面里存在「成规模的结果表格」。
   阈值 5 的边界经实测标定：登录页 26 KB / 0 个 `<tr>`，0 结果页 17 KB / 0 个 `<tr>`，
   167 结果页 700 KB / **168** 个 `<tr>`。
2. **登录表单规则加内容排除**：只有**没有**成规模结果表格时，才用登录表单元素判 `needLogin`。
3. **新增 `__last_selector_compound(selector)`** 与 **`__detect_selector_mismatch(html_doc)`**：
   取行选择器末段（带 `.` 或 `#` 的复合选择器）；若**完整选择器命中 0、末段却能命中 >0**
   ⇒ 判定「站点改版（层级或列序变化）」。末段形如 `tr`（无 class/id）时一律不采信（没有区分度）。
4. **`parse()` 分流**：解析不出任何条目时，**先**跑选择器失配检测（报 `parseError` +
   「该站结构可能已改版，需更新索引器定义」），**再**回落到反爬/登录态分类。
   只有 `CFBlocked` / `needLogin` / `httpError` 才置 `is_error = True` 并打 WARNING。

## 验证

### 真机三层对照（NAS 生产容器 + 真实网络，走生产 `spider_search` 链路）

| 组合 | 条数 | state | 判读 |
|---|---|---|---|
| 旧代码 + 旧规则 | **0** | `needLogin`（页面包含登录表单…Cookie 可能已失效） | ❌ 逐字复现用户上报 |
| **新代码 + 旧规则** | 0 | `parseError`（该站结构可能已改版…需更新索引器定义） | ✅ 不再误报 |
| **新代码 + 新规则** | **100** | `None`（正常） | ✅ 修复生效 |

- 探针口径：`ProUser().get_indexer(url="https://mikanime.tv/")` 取索引器配置，
  `spider_search(TorrentSpider(), indexer, keyword="诛仙")` —— 与「站点体检 L5」**同一条链路**。
- 新规则的解析结果抽查：`size` 分别解析为 `549537710` / `244664238` / `1986422374` 字节
  （= 524.08 MB / 233.33 MB / 1.85 GB，与页面显示一致），`pubdate` = `2026/09/19 15:36`，
  `enclosure` 为完整磁链 —— 说明 `td:nth-child(3)` / `td:nth-child(4)` 取列正确。
- 100 条是 `pt.site_search_result_num`（默认 100）的**正常截断**；页面上实际有 167 行。
- 全程**零中断**：新文件只投放到容器 `/tmp`，用 `importlib` 动态加载新 `_spider.py`、
  用新 bin 手工构造 `IndexerConf`，**未覆盖任何生产文件、未重启容器**；验证后已清理。

### 真机只读探针（站点结构复核）

| 判据 | 本地夹具 | NAS 真机 |
|---|---|---|
| 页面大小 | 700 KB | **700490 B** |
| `<tr` 计数 | 168 | **168** |
| 旧选择器命中 | 0 | **0** |
| 新选择器命中 | 167 | **167** |
| `div.episode-table` 存在 | 是 | **是** |
| `name="username"` 存在（误判源） | 是 | **是** |

### 离线

| 层 | 内容 | 结果 |
|---|---|---|
| **端到端** | 真实加载新旧 `_spider.py` + 新旧 bin + 三个真实页面（搜索结果页 700 KB / 登录页 26 KB / 0 结果页 17 KB）；A 组新规则解析 100 行、字段零缺失；B 组旧规则报改版；C 组旧代码**逐字复现用户两行日志**；D/E 组阈值边界与末段取值 | **34/34** |
| **回归** | A1 抓取状态分类 36 项 / A3 搜索流程 53 项 / v5.1.2 搜索链路 36 项 / PTZone 端到端 | **36/36 · 53/53 · 36/36 · 通过** |

> 离线夹具全部取自**真实页面**，并按真机结构照抄；`num_filesize` 由 AST 从真实
> `string_utils.py` 抽取后局部执行（不手写桩），避免「宽松夹具 = 假绿」。

## 已知边界

- 蜜柑首页搜索一次最多返回 100 条（`pt.site_search_result_num` 控制），与本次修复无关。
- 选择器的**末段检测**只对「末段带类名/id」的站点有效；末段形如 `tr` 的站点不适用
  （此时退化为原分类逻辑），这是刻意的：没有区分度就不该据此改判。
- `browse`（无关键词列表浏览）不受影响：蜜柑的浏览页行类名为空，旧选择器本来也匹配不到。
- 该修复**不改变**任何站点的 Cookie 使用方式；公开站继续无需 Cookie。

# v6.3.7 — 绿联网页端「封面全黑」：错端点 getImageStream + 凭证走不了 header

## 现象

打开 **「我的媒体库 - 绿联影视」** 页面：六个媒体库卡片上方全是**黑色方块**；
库内条目封面、「正在观看」「最近添加」的缩略图同样不显示。
`<img>` 请求返回 HTTP 200，所以前端不报错，只是**画不出图** —— 典型的「200 但不是图片」。

## 根因（真机取证，四个探针逐层钉死）

### R8 · 网页端图片走的是错端点，且完全没带凭证

绿联的图片接口需要**登录凭证**，而浏览器 `<img>` 带不上请求头 ⇒ 这类图片统一走
NAStool 自己的 `/img` 中转（`get_nt_image_url()` 生成 `img?url=<内层地址>`）。
内层地址由 `get_local_image_by_id(remote=False, inner=True)` 拼接，旧实现是：

```python
image_url = f"{self._host}ugreen/v1/video/getImageStream?name={path}&size=1"
```

两个问题叠加，真机实测：

| 实测项 | 结果 |
|---|---|
| `getImageStream`（**少一个 a**，无凭证） | 200 + `{"code":9405,"msg":"","data":{},"err_data":{"app_id":"com.ugreen.videomgr"}}`，**77 字节 JSON** |
| `getImaStream`（正确端点）**无凭证** | 200 + `{"code":9405,...}`（107 字节 JSON） |
| `getImaStream` + **query `token`/`ugk`** | 200 + **真 JPEG**（`\xff\xd8\xff`，19155 字节起） |

⇒ `/img` 中转拿到的是一段 JSON，原样吐给浏览器 ⇒ **画不出图 = 黑块**。
这条链路同时服务**条目封面、媒体库封面、正在观看、最近添加**，
所以症状是「整个网页端的封面全黑」，不是单个页面。

### R9 · 凭证**只能走 query 参数**（不能照搬飞牛那套注入 header）

`/img` 中转的既有机制是「客户端提供 Cookies → 中转时带上」（v6.2.6 为飞牛影视做的）。
绿联**不适用** —— 实测把凭证放在 header / cookie 里全部无效：

| 凭证传递方式 | 结果 |
|---|---|
| header `Ug-Token` / `Token` / `Authorization` / `x-token` | ❌ 均返回 9405 |
| cookie `token=...` | ❌ 返回 9405 |
| **query `token=` 或 `ugk=`** | ✅ 返回真 JPEG |

⇒ 绿联必须把凭证**拼进 URL**（`get_image_stream_url()` 已按 `_is_ugk` 自动选参数名）。

### R10 · 编码层数只能**一层**（踩过一次）

`get_image_stream_url()` 内部用 `urlencode` 把 `name` 编码（空格→`+`、`/`→`%2F`），
`get_nt_image_url()` 再整体 `quote` 一次。实测：

- **单层**编码（即 `get_image_stream_url()` 的产出）：**5/5** 取到 JPEG
- **双重**编码（外面再 `quote` 一次）：**0/5**（返回 201 + 155 字节，非图片）

⇒ 正确做法就是**直接复用 `get_image_stream_url()`**，不要自己再拼一次 URL。

### R11 · 「我的媒体库」卡片从来就没有封面数据

`web/main.py:298` 把 `MediaServer().get_libraries()` 的返回直接喂给
`web/templates/index.html`，模板逻辑是：

```jinja
{% if Library.image %}<custom-img img-src="{{ Library.image }}">
{% else %}<custom-plex-library-img img-src-list='{{ Library.image_list }}'></custom-plex-library-img>{% endif %}
```

而绿联的 `get_libraries()` 返回的字典**只有 id / name / type / path / link** ——
`image` 与 `image_list` **两个都没有**（`image_list` 是 Plex 专用多封面机制），
于是渲染出一个空组件 ⇒ 黑块。与其他客户端（Emby/Jellyfin 都给 `image`）不一致。

### 结构事实（本次实机探明）

`v1/video/homepage/media_list` 的每条库**自带封面字段**：

```
{media_lib_set_id, media_name, video_count, order_sn,
 poster_paths: [3 条], backdrop_paths: [3 条], custom_cover}
```

- `poster_paths` 的值有两种形态：**本地路径**（`/volume5/video/.../poster.jpg`）
  与**云端地址**（`https://scraper.ugnas.com/...webp?auth_key=...`）。
- ⚠️ 云端地址**带时效签名**，实测已过期 ⇒ **403**，不能直接用。
- ⚠️ **有些库的 `poster_paths` 全是云端地址**（实测「动漫电视剧」），
  这类库必须另找本地封面 —— 用 `poster_wall/media_lib/get_folder` 的
  `folder_arr[].cover`（**本地路径**，实测可用）。
- `folder_arr[]` 每条还带 `covers`（3 条）与 `backdrop_path`，本实现只取 `cover`。

## 改动（`app/mediaserver/client/ugreen.py`，+130/−7）

| 方法 | 状态 | 说明 |
|---|---|---|
| `_image_url_by_path` | 新增 | 按「本地图片路径 → 可用图片地址」统一构造（复用 `get_image_stream_url()`），`inner=True` 时包成 `/img` 中转地址 |
| `_library_cover_path` | 新增 | 为媒体库挑一个**可用的本地**封面路径：`custom_cover` → `poster_paths` 首个本地 → `folder_arr[].cover` → `backdrop_paths` 首个本地；**跳过**所有 http(s) 云端地址 |
| `get_local_image_by_id` | 修正 | inner 分支改用 `getImaStream` + query 凭证（此前是错端点 + 无凭证） |
| `get_libraries` | 补充 | 返回里新增 `image` 字段（此前完全没有） |
| `_load_library_paths` | 补充 | 在**同一次** `poster_wall_get_folder` 请求里顺带收集 `folder.cover`（零额外请求） |
| `_lib_covers` / `init_config` | 新增 | 库封面缓存及其重置（与 `_lib_paths` 同生命周期） |

## 验证

| 层 | 内容 | 结果 |
|---|---|---|
| **真机** | NAS 容器内投放新代码，对**六个库的封面**逐个还原成 `/img` 真实请求，断言拿到真 JPEG（`\xff\xd8\xff` 且 >3KB）；六个库首条条目封面同样断言 | **22/22** |
| **真机回归** | v6.3.6 的剧集查询/缺集判定/电影查重（宙斯之血 S1/S2、遗失的世界 3 季、反例） | **18/18** |
| **离线** | 假绿联服务端（完整加密握手）+ **新增图片端点**：夹具照抄真机结构（库1 全本地 / 库2 本地+远程 / 库3 全远程），断言库封面逐一命中预期来源、端到端拿到 JPEG、兜底路径生效 | **43/43** |
| **离线反向对照** | ① 换回错端点 `getImageStream` ⇒ 取不到图；② 剥掉凭证 ⇒ 取不到图；③ 清空 `folder.cover` ⇒ 全远程的库**彻底没有封面**（证明兜底确实在起作用） | 通过 |
| **离线回归** | 原 v6.3.6 剧集链路（52 项）/ v6.3.5 采集链路（132 项）/ 大库 4200 条（6 项）/ 跨页重叠（23 项） | **52/52 · 132/132 · 6/6 · 23/23** |
| **发版核验** | 四件套一致性、栏目结构、README 2.x 严格递增、行尾索引、改动面锚点、零丢失 | 见核验脚本 |

## 已知边界

- **凭证写在封面 URL 里**：这与 Emby/Jellyfin 的 `?api_key=` 做法一致（都是把长期
  凭证拼进图片地址）。若绿联登录态失效，需**刷新页面**重新渲染地址；容器无需重启。
- **「正在观看 / 最近添加」未端到端验证**：本机绿联该两个接口返回 0 条（无观看记录），
  但走的是**同一条已修好的封面链路**，且离线夹具已覆盖。
- 云端海报地址（`scraper.ugnas.com`，带 `auth_key`）**一律不使用**：实测已过期返回 403，
  且签名会持续过期，不可靠。
- 库封面取的是**该库内某一条目的海报**（绿联不提供「库专属封面图」，`custom_cover` 为空），
  因此同一部片的海报可能同时出现在库封面与条目列表里 —— 这是绿联数据模型的固有现象。

# v6.3.6 — 绿联影视「剧集查询 / 电影查重」恒为空：getTV 的真实键与被忽略的 search

## 现象

v6.3.5 修好媒体库同步（956 条全部入库）之后，另外两处功能仍然查不到东西：

1. **剧集缺失检测**：`get_no_exists_episodes()` 总是报「整季一集都没有」
   ⇒ 已下载完的剧被判为缺集，可能**重复下载整季**。
2. **电影下载查重**：`get_movies()` 恒返回空 ⇒ 媒体库里已有的电影被判为
   「媒体库中不存在」，会被**重复下载**。

回退验证：`get_tv_episodes(item_id=...)` 与 `get_tv_episodes(title=...)` 都返回 `[]`。

## 根因（实机取证确证，非推测）

### R5 · `get_tv_episodes` 恒空 —— 三个独立成因叠加

| # | 成因 | 实机证据 |
|---|---|---|
| ① | `v1/video/all` 的 `search` 参数**被服务端完全忽略** | 传 `宙斯之血` / `宙斯之血 第 2 季` / `遗失的世界` / `宙斯`，返回的都是**同一批前 20 条**（`total_num=123` 全量默认序） |
| ② | 条目名带「第 N 季」后缀，`vi_name == title` 精确比较恒不成立 | 条目名 `宙斯之血 第 2 季` vs TMDB 标题 `宙斯之血` |
| ③ | 读**不存在的键** `episodes` / `episode_arr` | `getTV` 真实返回的顶层键是 `folder_paths / is_favorite / metadata_lock_status / play_status / season_info / select_folder / tv_info / ug_actor_arr / video_info` —— **没有** `episodes` |

`v2/video/details/getTV` 的真实结构与旧实现假设完全不同：

```
season_info = [ {season_num, ug_video_info_id, category_id, name}, ... ]   ← 季列表
tv_info     = [ {ug_television_episode_id, episode, ep_name,
                 category_id, cover_path, file_info}, ... ]                 ← 集列表
video_info  = { ug_video_info_id, name, season, type, media_lib_set_id, ... } ← 该季元数据
```

两条容易踩的细节：
- `tv_info` 里**没有** `season_num` 字段（季节号只能从 `video_info.season` 或
  `ep_name` 里的 `SxxExx` 解析）；
- `folder_path` 参数（`ALL` / `''` / `'1'`）**不影响** `season_info` 与 `tv_info` 的返回。

### R6 · `get_movies` 恒空 + 一处 `or` 误用

- 与 R5 同根因：也靠被忽略的 `search` 参数 ⇒ 恒空 ⇒ 下载查重失效、重复下载。
- 另有独立缺陷：命中判断写成

  ```python
  if name == title or (year and str(video_info.get("release_year")) == str(year)):
  ```

  用 `or` 连接「片名相同」与「年份相同」⇒ **年份相同但片名不同**的电影也会被判为
  「已存在」（假阳性，会拦住本该下载的片子）。

### R7 · 本次补丁自身引入的回归（真机实测抓到，已在发版前修掉）

补丁的插入锚点漏掉了 `@staticmethod` 装饰器，于是原本属于 `_flatten_item` 的
`@staticmethod` 被「吃掉」去装饰了新加的 `_normalize_title` ⇒ **`_flatten_item`
退化成实例方法** ⇒ `self._flatten_item(video)` 把 client 实例当成了 `video` 参数
⇒ `AttributeError`，**并连带打崩 `get_items`（媒体库同步）**。

判据因此升级：**用 AST 逐方法比对「补丁前基线」**，断言
「没有任何既有方法的装饰器集合发生变化 + 没有方法消失」—— 比字符串检查可靠得多。

### 结构事实（本次实机探明，供后续参考）

- `ug_video_info_id` 的粒度是**季**，不是「剧」：同一部剧每一季都是一条独立条目
  （实测「宙斯之血 第 1 季」`id=681`、「第 2 季」`id=677`）。
- 条目外层**没有** `season` 字段；季号要从条目名、`video_info.season` 或 `ep_name` 拿。
- 「遗失的世界」`season_info` 有 3 季，逐季 `tv_info` 分别 20 / 22 / 21 集。

## 改动（`app/mediaserver/client/ugreen.py`，+210/−75）

| 方法 | 状态 | 说明 |
|---|---|---|
| `_normalize_title` | 新增 | 去掉「第 N 季」/「Season N」/尾部年份括号，用于片名比对 |
| `_title_matches` | 新增 | 归一化后相等即视为同一部剧 |
| `_parse_season_num` | 新增 | 从条目名解析季号（解析不到返回 0） |
| `_parse_season_episode` | 新增 | 从 `ep_name` 的 `SxxExx` 解析 (季, 集)，作兜底 |
| `_find_tv_candidates` | 新增 | **本地全量条目**里按片名找剧集候选（多季返回多条），year 只做排序偏好 |
| `get_tv_episodes` | 重写 | 季列表取自 `season_info`、集列表取自 `tv_info`；季号优先 `video_info.season`；season 指定时先按条目名预筛再请求；返回 `[{season_num, episode_num}]` |
| `get_movies` | 重写 | 本地全量条目里按片名（type=1）匹配；修掉 `or` 误用 |
| `get_episode_image_by_id` | 修正 | 同源缺陷：改读 `tv_info`，集封面优先用 `cover_path` |

## 验证

| 层 | 内容 | 结果 |
|---|---|---|
| **真机** | NAS 容器内投放新代码跑真实绿联服务端：多季剧逐季（宙斯之血 S1=8 集/S2=8 集；遗失的世界 20/22/21）、item_id 直传、缺集判定（total=10 ⇒ 缺 [9,10]）、电影查重命中、反例不乱报 | **18/18** |
| **真机回归** | v6.3.5 的媒体库同步（R7 曾打崩的那条链路）：956 条、逐库 451/85/17/17/365/21 | **21/21** |
| **离线** | 假服务端按真机结构造多季夹具（`season_info`/`tv_info`/`video_info`），覆盖纯函数、多季取季、缺集判定、电影查重、反例、AST 装饰器、源码级判据 | **52/52** |
| **离线反向对照** | 把夹具的 `getTV` 换回旧的 `episodes` 结构 ⇒ B/C 组 **15 项立即失败**，证明夹具不是「碰巧通过」 | 通过 |
| **离线回归** | 原 v6.3.5 采集链路（132 项）/ 大库 4200 条（6 项）/ 跨页重叠（23 项） | **132/132 · 6/6 · 23/23** |
| **发版核验** | 四件套一致性、栏目结构、README 2.x 严格递增、行尾索引、改动面锚点、零丢失 | 见核验脚本 |

## 已知边界

- 片名匹配是**归一化后相等**（与原实现同为精确匹配口径）。若绿联刮削名与 TMDB
  标题有实质差异（例如带副标题），仍可能匹配不到 —— 这属于「匹配口径」议题，
  不是本次的「读错字段 / 参数被忽略」缺陷。
- `get_episode_image_by_id` 仅在 webhook 通知链路使用（绿联的
  `get_webhook_message` 尚未实现），本次按同源缺陷一并修正，**未在真机走通完整链路**。
- 多季剧中若库里只入库了某一季，查询其它季会**正确返回空**（不是缺陷）。

# v6.3.5 — 绿联影视「媒体库同步数量 0」：条目采集按 media_lib_set_id 分桶重写

## 现象

在「设置 → 媒体服务器 → 绿联影视」点「测试」已经通过，点「开始同步」后弹窗显示
**「媒体库数据同步完成，同步数量：0」**。日志里媒体库列表是正常的：

```
发现媒体库：id=1, name=电影, type=, path=          ← type 与 path 都是空的
...（6 个库同样）
获取到 6 个媒体库
过滤后需要同步 6 个媒体库
媒体服务器统计：MovieCount=833, SeriesCount=123     ← 绿联侧确实有 956 条
get_items: 媒体库 电影 的路径为空，无法遍历          ← 每个库都命中这句
媒体库 电影 已处理 0 个条目
媒体库数据同步完成，同步数量：0
```

## 根因（实机取证确证，不是推测）

### R1 · `media_list()` 不返回 `path`，而遍历入口就是 `path`

`v1/video/homepage/media_list` 返回的 `media_lib_info_list` 元素**只有**
`media_lib_set_id` / `media_name` / `video_count` / `poster_paths` —— **既没有
`path` 也没有 `media_lib_type`**。而旧实现是：

```python
lib_path = target_lib.get("path", "")
if not lib_path:
    log.warn("get_items: 媒体库 X 的路径为空，无法遍历")
    return []                     # ← 每个库都在这里退出，且只打 log.info/warn
```

于是 6 个库全部返回空，累加出来的 `total_count` 恒为 0 —— 而 `get_items()` 返回空
**不报错**，页面上看起来是「同步成功」。

路径要从**另一次请求**里取：不带 `path` 调
`v1/video/poster_wall/media_lib/get_folder`，其 `folder_arr` 的元素带
`media_lib_set_id` 与 `path`。实测 8 条对应 6 个库 —— **一个库可以有多个根**
（电影 = 华语电影 + 外语电影，电视剧 = 国产剧 + 欧美剧），只取一条会整块漏掉。

### R2 · 条目 `type` 也在元素外层（`video_info` 为空字典）

`v1/video/all` 的条目把字段**平铺在元素外层**：`type` / `media_lib_set_id` /
`ug_video_info_id` / `name` / `file_path` …，而 `video_info` 子字典实测是
**空 dict `{}`**。旧实现读 `video_info.type` ⇒ 恒为 0 ⇒ 即便能遍历，
也会被 `if video_type not in [1, 2]` 全部跳过。

### R3 · 目录树遍历代价过高

`v1/video/all` 的 `pageSize` **上限是 20**（传 100/200/500/1000 都只回 20 条），
且 `media_lib_set_id` 筛选参数**被服务端忽略**（传 1/3/5 都返回全量 833 条）。
好处是每条 item 自带 `media_lib_set_id` ⇒ 全量拉回后**本地分桶**即可精确归库。

### R4 · `v1/video/all` 分页「跨页重叠」⇒ 静默漏采 6 条

修完 R1/R2/R3 之后真机实测：6 个库都拿到条目了，但**总数是 950，而绿联自己报 956**。

逐页复算（真机 2026-10-03，完全稳定复现，不是网络抖动）：

```
电影类: 服务端 total_num=833 | 逐页实收 833 条 | 去重后 827 条   ← 少了 6 条
剧集类: 服务端 total_num=123 | 逐页实收 123 条 | 去重后 123 条
```

重复的 6 个 id（870 / 848 / 825 / 750 / 683 / 617）**全部落在相邻页边界**：

```
id=870 出现在页 [4, 5]      id=750 出现在页 [9, 10]
id=848 出现在页 [5, 6]      id=683 出现在页 [12, 13]
id=825 出现在页 [6, 7]      id=617 出现在页 [15, 16]
```

原因：`v1/video/all` 的页窗口是按**排序键**切的。`sort_type=2`（服务端默认，也正是
原实现的写法）是「最近播放 / 最近更新」，**存在大量并列值** ⇒ 服务端切页不稳定，
相邻页会重叠 1 条；重叠占掉的位置把末尾条目挤出了所有页窗口，**永远拉不到**。

这个缺陷**没法被原来的判据发现**：服务端 `total_num` 把跨页重复也算进去了（仍报
833），所以「实收条数 == total_num」恒成立 —— 原来那句「全量条目可能被截断」的
WARN 永远不会触发，漏采完全静默。

换排序键实测（同一台机器、同一时刻）：

| sort_type | total_num | 逐页实收 | 去重后 | 结论 |
|---|---|---|---|---|
| 2（服务端默认，原用） | 833 | 833 | **827** | 跨页重叠，漏 6 条 |
| 2 配 order_type=1 | 833 | 833 | 830 | 仍重叠 |
| **1** | 833 | 833 | **833** | ✅ 零重叠，取全 |
| **3** | 833 | 833 | **833** | ✅ 零重叠，取全 |
| 0 | — | — | — | `code=1005` 参数错误 |

修复：

1. `_api.video_all()` 增加 `sort_type` / `order_type` 参数，**默认值改为稳定值 1**。
   `sort_type=2` 只留给 `get_resume` / `get_latest` 这类「最新」语义的**单页**请求
   —— 它们不翻页，不存在窗口重叠问题。
2. 抽出 `_fetch_classification()`，只认**稳定排序**；并且把「拉完了没有」的判据从
   **原始累计条数**改成**去重后的条数** —— 后者才是真判据。
3. `_all_library_videos()` 取完后**拿 `total_num` 校验**：不足则换 `sort_type=3`
   补拉一轮（只补缺、不重复计），仍不足就打
   「全量条目仍缺 N 条：接口报 X 条，实得 Y 条」，不再静默。

> 这条缺陷**假服务端测不出来**（假服务端分页是稳定的）—— 是**真机验证**才暴露的。
> 也说明「离线全绿」不等于「真机正确」，发布前的真机取样不可省。

## 改动

| 新增方法 | 作用 |
|---|---|
| `_load_library_paths()` | 不带 `path` 调 `poster_wall_get_folder`，从 `folder_arr` 建 `库ID → [根路径, ...]`（支持一个库多个根） |
| `_all_library_videos()` | `v1/video/all` 按 `classification=-102/-103` 全量分页拉取；以 `total_num` 校验完整性，不足则换备用排序补拉 + WARN（见 R4） |
| `_fetch_classification()` | 单分类逐页拉取的实现：**只用稳定 `sort_type`**、按 id 去重，进页判据用**去重后**条数，翻页上限按 `total_num` 动态放宽 |
| `_walk_library_videos()` | 兜底：条目不带 `media_lib_set_id` 的老固件退回目录树 BFS（收 `video_arr` + `folder_arr` 入队下钻） |
| `_flatten_item()` | 把内层 `video_info` 与外层条目**摊平成一份**（外层优先），兼容两种固件结构 |
| `_video_info_cached()` | 关键字段缺失时回源 `v1/video/info` 补齐，同一部片只请求一次 |
| `_infer_library_type()` | 库类型由名称推断（绿联不返回库类型） |
| `STABLE_SORT` / `FALLBACK_SORT` | 全量分页用的排序键 `(1,2)` / `(3,2)`；注释里写明为什么不能用服务端默认的 2 |

要点：

1. **主通道 = 全量拉取 + 本地按库分桶**。实测 49 次请求
   （`ceil(833/20) + ceil(123/20)`）拿全 956 条，而目录树 BFS 要 **900+ 次**
   —— 因为实测根目录下 `video_arr=0`、`folder_arr=17`，**每个影片目录都要单独请求一次**。
   分页排序键必须稳定（`sort_type=1`），否则会**跨页重叠导致静默漏采**（见 R4）。
2. **兜底通道 = 目录树 BFS**，用于「条目不带 `media_lib_set_id`」的老固件；
   主通道分桶为空时自动切换，并在日志里说明用了哪条。
3. **字段读取兼容两种固件结构**。旧版本固件把详情包在 `video_info` 子字典里，
   新版本平铺在外层。只认一种就会在另一种上出现「同步成功但字段全空」的
   **静默降级** —— `get_local_image_by_id()` 内部只认 `info["poster_path"]`，
   所以交给它的也必须**摊平后**的 dict，否则图片恒为空。
4. **类型推断顺序「先电影、后剧」**。上游 MoviePilot 的 `__infer_library_type`
   把「剧/综艺/动漫/纪录片」放在前面，会把**「动漫电影」判成剧集**，本仓不跟。
5. **「取全了没有」的判据必须是去重后的条数**。用原始累计条数会被跨页重复骗过
   （R4）—— 服务端把重复也算进 `total_num`，于是「实收 == total_num」恒成立，
   告警永远不触发。

### 同源缺陷一并收口

以下方法原来**只读内层 `video_info`**，在同一台固件上全部静默返回空：

| 方法 | 影响 |
|---|---|
| `get_movies()` | 下载前查重（`downloader.py:1758`）⇒ 失效会导致**重复下载** |
| `get_resume()` | 首页「继续观看」卡片（`web/main.py:307`）一直是空的 |
| `get_latest()` | 首页「最近添加」卡片（`web/main.py:310`）一直是空的 |
| `get_tv_episodes()` 搜索分支 | 按标题查剧集时 |

统一改用 `_flatten_item()` —— 对老固件结构**行为完全不变**。

## 验证

- **真机复验（2026-10-03，NAS 192.168.3.3 上的 nas-tool 容器内直接驱动真实绿联服务端）**：
  `_test_ugreen_635f.py` **21/21**。服务端 `total_num` 833+123=956，客户端取全 956 条、
  id 零重复，逐库 451 / 17 / 365 / 85 / 17 / 21，未触发任何「补拉 / 仍缺」告警；
  且库 1 与库 5 的条目路径与各自根目录**零错配**（证明按 `media_lib_set_id` 分桶与
  绿联自己的归类完全一致）。修 R4 之前同一脚本实测为 950 条（电影 448 / 儿童电影 362）。
- `_verify_ugreen_635f.py`：**23/23**。假服务端按 `sort_type` 分流，**逐项复现真机的
  跨页重叠**（实收 833 / 唯一 827 / 尾部 6 条永不出现，与真机数字完全一致），然后：
  - 正向：客户端只用 `sort_type=1`、取全 956、逐库条数与真机一致、无告警；
  - **反向对照**：把 `STABLE_SORT` 强制改回 `(2,2)` ⇒ 复现 827 条，并**必须**打出
    「全量条目仍缺 6 条」告警 —— 证明这是真正的兜底，而不是碰巧躲过；
  - 边界：翻页请求数恰为 49、`pageSize` 恒为 20、4200 条大库不被写死页数截断。
- `_verify_ugreen_635.py`：**132/132**。本地起假绿联服务端，实现完整加密握手
  （`x-rsa-token` 发公钥、`X-Ugreen-Security-Code` 带 RSA 加密的 aes_key、
  `encrypt_query` 用 AES-GCM），假服务端**用私钥解出 aes_key 后按真实参数路由**
  ⇒ 端到端，不是打桩。数据形状全部来自实机取证。
  - 覆盖 **4 种固件结构**：外层字段齐全 / 外层字段贫瘠（逐条回源 956 次）/
    详情在 `video_info` 子字典（0 次回源）/ 条目不带 `media_lib_set_id`（走 BFS 兜底）
  - 逐库条数断言 = 真机实测值（451 / 85 / 17 / 17 / 365 / 21，合计 956）
  - `video/all` 翻页断言 = 49 次；二次调用走缓存；`init_config()` 后缓存重置
  - 字段完整性：title / year / tmdbid / image / path / link / library / type
  - 首页卡片与下载查重的 3 组 × 5 项断言
  - **62 个未改动方法逐字节相同**；改动范围仅限 8 个预期方法；新增方法仅限本版
    新写的 8 个采集链路方法
  - v6.3.4 的 `last_error` 失败路径 5 项不回归
- `_verify_ugreen_635_huge.py`：**6/6**。造一个 4200 条的媒体库，断言真的翻 210 页
  全拉回来（旧写死 `page <= 200` 会静默截断在 4000 条）。
- `py_compile` 通过。

## 已知边界

- **`get_tv_episodes()` 在真机上仍返回空**（本版**未修**，属独立缺陷）：
  绿联的剧集详情接口 `v2/video/details/getTV` **不返回 `episodes` / `episode_arr`**，
  真实键是 `season_info`（含 `season_num`）+ `tv_info`（含 `episode`、`file_info`）；
  降级路径 `v1/video/info` 对该 id 直接返回 `None`。影响：按标题查剧集 / 缺失剧集
  查询拿不到集列表（**与媒体库同步无关**）。修它需要先摸清 `folder_path` 语义
  （`getTV` 一次只回当前选中目录的集），留待后续版本。
- `pageSize` 上限 20 是服务端限制，条目特别多的库（数千条）同步会花几十秒到几分钟。
- 「测试连接」失败原因的显示沿用 v6.3.4 的 `last_error`，本版未改动。
- 真机验证是**临时把新 `ugreen.py` 投放到容器内**跑的（跑完已还原），正式生效仍需
  `docker compose pull && docker compose up -d` 升级镜像。

# v6.3.4 — 绿联影视「测试连接」失败原因回显（last_error 诊断闭环）+ UG-Client-Id

## 现象

在「设置 → 媒体服务器 → 绿联影视」点「测试」，只显示一句 **「测试失败！」**，
没有任何原因，无法判断是地址错、端口错、密码错还是接口变更。

## 根因

「测试」按钮走的是：

```
test_command = app.mediaserver.client.ugreen|UgreenClient
  → UgreenClient() → init_config() → _connect()      # 真正去登录
  → get_status()
```

而 `web/action.py::__test_connection` 早在 **v6.2.5** 就支持把客户端的 `last_error`
回显到页面 —— 但当时只覆盖了飞牛影视：

| 客户端 | 文件内 `last_error` 出现次数 |
|---|---|
| 飞牛影视 `trimemedia.py` | 38 |
| 绿联影视 `ugreen.py` | **0** |

且 `UgreenClient.get_status()` 是：

```python
if not self._host or not self._api:
    return False
try:
    user = self._api.current_user()
    return user is not None
except Exception:
    return False                      # ← 异常被吞掉，连日志都没有
```

⇒ 失败原因只写进容器日志，页面上只剩一句「测试失败！」。

## 改动（对齐 MoviePilot 上游 `app/modules/ugreen`）

### 1. 补 `last_error`，覆盖全部失败出口

`UgreenClient` 与 `_UgreenApi` 都增加 `last_error`，每次失败都写具体原因：

| 出口 | 页面显示的原因 |
|---|---|
| 没读到配置 | 未读到「绿联影视」配置：请先在 设置 → 媒体服务器 → 绿联影视 里… |
| 配置不完整 | 配置不完整，缺少：地址、用户名 |
| 缺 cryptography | 缺少 cryptography 库（镜像内未安装）… |
| 地址/端口不通 | 获取登录公钥失败：请求 `<url>` 无响应或异常（`ConnectionError: …`） |
| 端口连到了别的服务 | 获取登录公钥失败：返回非 JSON 响应（HTTP 200，`<url>`），Content-Type：text/html，响应：`<html>…` |
| 取公钥失败（业务码） | 获取登录公钥失败：`<msg>`（`<url>`） |
| 服务端未给公钥 | 获取登录公钥失败：服务端未返回 RSA 公钥（接口版本可能已变更） |
| 密码错 / 登录被拒 | 登录失败：`<msg>` |
| 未返回 token | 登录失败：服务端未返回 token/public_key（接口版本可能已变更） |
| token 不被接受 | 连接中断：`<api.last_error>` |
| 业务接口 code 非 200 | 接口 `<path>` 返回 code=`<code>`：`<msg>` |

`get_status()` 改成三段式（地址 → 账号 → 未建立连接），并且**优先保留**
`_connect()` 写入的更具体原因，不被通用文案覆盖 —— 否则「地址填错」会被
一句「未建立连接，请检查地址、端口、用户名与密码」盖掉。

### 2. 「网络层失败」与「非 JSON 响应」拆成两段

`login()` 的取公钥请求与 `_request_json()` 原先都是：

```python
check_json = check_resp.json()      # 网络异常和非 JSON 都落进同一个 except
```

只报 `JSONDecodeError: Expecting value` —— 既没有状态码，也没有 Content-Type，
更没有响应片段，拿到这句话没法定位。现在拆成：

- 网络层异常 → 原样带上 `type(err).__name__` 与 `err`（**Errno 113/111/110 就在这里**）；
- HTTP 已返回但非 JSON → 打出状态码 + Content-Type + 响应前 200 字。

**Errno 判读**：`113` = 连机器都没找到（IP 错 / 设备离线 / 跨 VLAN）；
`111` = 端口没人监听（服务没起）；`110` = 被防火墙 DROP。
出现 Errno 时协议、路径、签名、账号全都还没轮到 —— 别去改代码。

### 3. 补 `UG-Client-Id` 请求头

上游 `_common_headers()` 里 `Client-Id` 与 `UG-Client-Id` **同值**，
但只有登录阶段的明文请求带（`crypto.build_headers()` 里没有）。本仓原缺这一行，
照上游补齐。**加密请求头不加** —— 与上游逐字节对齐。

### 4. 修掉重复文案

`_connect()` 原先把 `api.last_error` 又套了一层 `f"登录失败：{...}"`，
而 `api.last_error` 在「密码错误 / 未返回 token」这两条路径上本身就带
「登录失败：」前缀 ⇒ 页面显示「登录失败：登录失败：密码错误」。去掉外层前缀。

## 验证

- `_verify_ugreen_634.py`：**48/48**
  - A 结构 15 项（含「无 `登录失败：登录失败：` 重复前缀」）
  - B 与上游 `_common_headers` 键集合逐项一致、加密头不含 `UG-Client-Id`
  - C 行为 18 项：**本地假服务端**真实跑登录链路，覆盖
    端口无人监听 / check 业务码非 200 / 无公钥 / login 业务码非 200 /
    无 token / 全通 / 非 JSON 响应 / 空 host / 缺账号 / 保留具体原因 / 业务接口 401
  - D 请求头实证 5 项：`check`/`login` 带 `UG-Client-Id` 且与 `Client-Id` 同值；
    加密请求不带但带三个安全头
  - E 向后兼容 3 项：**62 个未改动方法逐字节相同**、方法总数不变
- `_regress_633.py`：回归失败 0 个（含 `_verify_bracket_632.py` 21/21、
  飞牛 4 个脚本全绿、`_verify_mediasync_clients_v608.py` 37/37）
- `py_compile` 通过

## 已知边界

本次只做「让失败可见」，**没有**动协议、加密、接口路径。若真因是
「地址端口填 9443」（绿联影视的 Emby 兼容端口），那属于**另一条通道**
（上游按端口 9443 自动切到 `/emby/...` + `AuthenticateByName`，本仓未实现）
—— 等这条原因显示出来后再决定是否补。

# v6.3.3 — 文件转移「只处理了几个」根因收敛：最小文件大小 + 转移预检

## 现象

用户报告：在「文件管理 → 转移」里整理一个目录，界面显示 **`总数: 5`**、**`失败: 0`**，
但该目录下**实际有 26 个视频文件，且都大于 150M**。

紧接着又发现关键事实：**在「手动识别」弹窗里把「最小文件大小」填成 `0` 之后就正常了**，
26 个全部进入识别。

—— 这就是根因所在。

## 根因：留空 ⇒ 静默套用配置里的 150MB

配置项与代码路径：

```
config/config.yaml:92          media.min_filesize: 150                       # 默认 150MB
web/templates/setting/basic.html  「基础设置 → 转移最小文件大小(MB)」，placeholder 写 200
app/conf/... -> config.py:27   RMT_MIN_FILESIZE = 150 * 1024 * 1024
app/filetransfer.py:48         _min_filesize = RMT_MIN_FILESIZE（类默认值）
app/filetransfer.py:117-121    init_config() 里用 media.min_filesize 覆盖 self._min_filesize
```

弹窗参数进入 `transfer_media` 后的判定（`app/filetransfer.py:589-595` 一带）：

```python
if str(min_filesize) == "0":
    now_filesize = 0
else:
    now_filesize = self._min_filesize if not str(min_filesize).isdigit() \
        else int(min_filesize) * 1024 * 1024
```

**留空时 `min_filesize` 是空字符串**，而 `str("").isdigit() == False`
⇒ 走前半支，直接把配置里的 `_min_filesize`（150MB）当成阈值。

于是扫描阶段 `app/utils/path_utils.py:30`：

```python
if filesize and os.path.getsize(cur_path) < filesize:
    continue          # ← 文件在这里被丢弃，且不留任何痕迹
```

## 为什么「失败: 0」—— 丢弃发生在记账之外

`get_dir_files` 有 4 道过滤：**非法路径 / 集数格式 / 后缀 / 大小**。
在这 4 道之前被 `continue` 掉的文件，**既不在 `Medias` 里、也不在 `file_list` 里**，
而 v6.3.1 新增的 `missed_files`（`app/filetransfer.py:655` 一带）是
`[f for f in file_list if f not in Medias]` —— 它看得见「识别阶段被跳过」的，
**看不见「扫描阶段就被丢掉」的**。所以界面上只剩「失败: 0」，一个提示都没有。

`总数` 的语义也一并证实：`total_count` 在 `for file_item, media in Medias.items()` 内
（`app/filetransfer.py:678` 附近）逐条 +1，全仓只此一处写出 `总数`
⇒ **`总数: 5` ⟺ `len(Medias) == 5`**。

### 排除掉的错误假设

上一轮曾怀疑「扫描阶段静默丢文件是别的规则」。用 `artifacts/_probe_transfer_scan_633.py`
以真实 `PathUtils` / `EpisodeFormat` 建 26 个临时文件跑完整扫描链，结论：

> 扫描阶段**只能产出 26 或 0**，绝不可能给 5。
> —— `EpisodeFormat.match()`（`app/utils/episode_format.py:50-52`）首行是
> `if not self._format: return True`；**任何非空的集数格式**都会让
> `parse.parse(self._format, file)` 整串匹配失败，把 26 个全部挡掉；
> 空格式则 26 个全放行。`is_invalid_path` 在同一目录内也是同判据，同样只会「全合法 / 全非法」。

所以「26 → 5」这种**部分丢弃**只能来自**逐文件的大小判据**，与用户实测完全吻合。

## 改动清单（6 文件）

| 文件 | 改动 |
|---|---|
| `app/utils/path_utils.py` | `get_dir_files` 增加 `detail=False` 参数；`detail=True` 时返回 `(命中列表, [(路径, 原因)])`。**默认行为与旧版逐字节一致**（14 个调用点均未受影响） |
| `app/media/media.py` | 新增 `get_local_media_info(file_path)`：把「文件名 → 上级目录 → 上上级目录」的本地识别**逐行等价**抽为独立方法；`get_media_info_on_files` 改为调用它 |
| `app/filetransfer.py` | ① 扫描改用 `detail=True`，对被过滤文件写 `log.warn`（数量+文件名+原因）；② 新增 `preview_transfer()`（只读预检） |
| `web/action.py` | 注册 `preview_transfer` 路由 + 新增 `__preview_transfer`（路径来源与 `__rename` 完全一致：`logid` → `unknown_id` → `inpath`） |
| `web/static/js/functions.js` | 「最小文件大小」默认填 `0`；弹窗打开时重置结果区；新增 `preview_media_transfer()` / `rename_preview_escape()` / `render_rename_preview()` |
| `web/templates/navigation.html` | 问号说明重写（`data-bs-html`）、placeholder 改 `0 = 不限制大小`、footer 新增「测试」按钮、body 末尾新增结果区 |

### 为什么把本地识别**抽出来**而不是重写一遍

预检必须与真实转移**同口径**，否则「测试说 26 个、转移还是 5 个」会二次伤害用户。
所以 `media.py` 的这段识别是**原样搬运**（`artifacts/_verify_preview_633.py` 的 B 段
把备份与当前的核心识别段**逐行比对**，证明 22 行完全相同），两处共用同一方法。

### 预检为什么**不能**走 TMDB

`get_media_info_on_files` 在搜不到时会写缓存 `{media_key: {'id': 0}}`
（`media.py:992` 一带，表示「这条已确认搜不到」）。预检若触发这条路径，
**会毒化随后的真实转移** —— 用户点完「测试」再点「转移」，
反而因为缓存被判「未识别」而失去 TMDB 补全。所以预检只做**纯本地识别**，
命中判据取 `get_media_info_on_files` 里完全相同的那一句：

```python
if not meta_info.get_name() or not meta_info.type:
    continue        # 不计入 Medias → 也不计入预检的 recognized
```

### 预检口径与界面「文件总数」严格同义（含疑似预告片）

复核时发现一处**一度写错**的地方并已修正：`transfer_media` 里

```python
for file_item, media in Medias.items():
    total_count = total_count + 1          # ← 第一句，先计数
    if not udf_flag:
        if re.search(r'[./\s\[]+Sample[/.\s\]]+', file_item, re.IGNORECASE):
            continue                        # ← 之后才跳过疑似预告片
```

即**界面「总数」包含疑似预告片**。若预检把 Sample 从 `recognized` 里剔除，
数字就会比真实界面少。现在的口径：

* `recognized` = 本地识别通过的文件数 = `len(Medias)` = 界面「总数」（**含**疑似预告片）；
* `sample_skipped` = 其中疑似预告片（仅在 `udf_flag=False` 时才会被跳过，与 `transfer_media` 同条件）；
* `bluray_skipped` = `get_media_info_on_files` 会跳过的「非目录但带蓝光原盘特征」的路径；
* `will_transfer` = `recognized - len(sample_skipped)`。

## 验证

`artifacts/_verify_preview_633.py` —— **45 / 45 全绿**：

| 组 | 内容 | 结果 |
|---|---|---|
| A | `get_dir_files` 向后兼容（`detail=False` 与旧版一致；守恒不变量「命中 + 被过滤 == 目录文件总数」） | 11/11 |
| B | 抽取等价性（备份 vs 当前，核心识别段**逐行相同**） | 4/4 |
| C | 真实本地识别 `[诛仙].Jade...` 能取到 name / type | 2/2 |
| D | AST/结构判据（`preview_transfer` 未写库未转移、路由、按钮、默认值 0 等） | 16/16 |
| E | 端到端真实调用：不限大小 → `scanned=26 recognized=26`；150MB 阈值 → `scanned=0 dropped=26`，原因全部指向「最小文件大小」 | 5/5 |
| F | **口径实证**：用**真实 `get_media_info_on_files`**（TMDB 全部桩掉）跑同一目录，证明 `recognized == len(Medias) == 26`；Sample 目录证明预告片计入 `recognized` 但不计入 `will_transfer` | 7/7 |

> 环境说明：本机 Python 3.13 + `guessit 3.7.1` 有两个与项目无关的适配点
> （`metavideov2.py` 内联 `(?i)` 不在表达式开头、`guessit` 用
> `with files('guessit.data') as d:` 的旧式上下文管理器）—— 验证脚本内做兼容处理，
> **未改动项目源码**。

存量回归：`artifacts/_regress_633.py` 复用 v6.3.2 的回归清单，失败 0 个。

# v6.3.2 — 修复：关闭「增强识别V2」时，[片名] 开头的中文资源识别错误

## 现象

「基础设置」里**关闭**「增强识别V2」后，片名写在方括号里的资源识别错误：

| 文件名 | 修复前 | 修复后 |
|---|---|---|
| `[诛仙].Jade.Dynasty.2024.S02E01.2160p.WEB-DL.H265.AAC-AilMWeb` | `Dynasty` | `诛仙` |
| `[斗罗大陆][第105集][1080p].mp4` | 空（「无法识别」） | `斗罗大陆` |
| `[电影名称] 2022 1080p WEB-DL H265.mkv` | `2022` | `电影名称` |
| `[Oppenheimer].2023.2160p.WEB-DL.H265.mkv` | `2023` | `Oppenheimer` |

打开「增强识别V2」时全部正确 —— 这两条路径用的是**两套**识别器。

## 调用链

```
web/templates/setting/basic.html:962   # checkbox id = laboratory.recognize_enhance_enable（显示名「增强识别V2」）
  → update_config → web/action.py:1313  set_config_value("laboratory.recognize_enhance_enable")
app/media/meta/metainfo.py:55-66        # 全仓唯一的分支点
  recognize_enhance_enable=True  → MetaVideoV2   （app/media/meta/metavideov2.py）
  recognize_enhance_enable=False → MetaAnime / MetaVideo（app/media/meta/metavideo.py）   ← 出问题的是这条
```

**分支判断本身没错**，错在 `MetaVideo` 那条分支内部的实现。

## 根因：一句无条件的 `re.sub` 把片名连同方括号一起删了

`app/media/meta/metavideo.py::MetaVideo.__init__` 原第 84-85 行：

```python
# 去掉名称中第1个[]的内容
title = re.sub(r'%s' % self._name_no_begin_re, "", title, count=1)   # _name_no_begin_re = r"^\[.+?]"
```

而分词器 `app/utils/tokens.py` 用的 `config.SPLIT_CHARS` **本来就包含 `[` `]` `【` `】`**：

```
SPLIT_CHARS = r"\.|\s+|\(|\)|\[|]|-|\+|【|】|/|～|;|&|\||#|_|「|」|~"
```

```python
splited_text = re.split(r'%s' % SPLIT_CHARS, text)
for sub_text in splited_text:
    if sub_text:
        self._tokens.append(sub_text)
```

也就是说 **`[Xxx]` 里的内容本来就会被单独切成一个 token**，这句删除并不是「为了能分词」，
它唯一的作用是**把方括号里的东西丢掉**。对发布组（`[VCB-Studio]`）是对的，
对「片名写在方括号里」的中文资源就是把片名一起吃掉了：

```
[诛仙].Jade.Dynasty.2024.S02E01.2160p.WEB-DL.H265.AAC-AilMWeb
  --删掉 [诛仙]-->  .Jade.Dynasty.2024.S02E01.2160p.WEB-DL.H265.AAC-AilMWeb
  token: Jade → Dynasty → 2024 → S02E01 → 2160p → WEB → DL → H265 → AAC → AilMWeb
  en_name = "Jade Dynasty"
  → __fix_name 里 `_name_nostring_re` 的 `^JADE`（翡翠台标签）把 "Jade" 又剥掉
  → 片名 = "Dynasty"（错），cn_name = None
```

`[斗罗大陆][第105集][1080p].mp4` 更直接：删掉 `[斗罗大陆]` 后只剩 `[第105集][1080p]`，
里面**没有任何能当片名的 token** ⇒ `get_name()` 为空。

## 「识别流程停止」发生在哪

`get_name()` 为空之后，下游 `app/media/media.py`：

```python
if not meta_info.get_name() or not meta_info.type:
    log.warn("未识别出有效信息：%s" % ...)
    return None        # 单个文件识别（文件管理页的「识别」按钮）→ 前端显示「无法识别」
    # 批量整理时这里是 continue → 该文件被静默跳过
```

所以用户看到的「停止」在 `media.py`，但**根因在上面那句无条件删除** —— 只要片名能取到，
这两个中止点都不会被触发。

## 改动清单（1 文件，`app/media/meta/metavideo.py`，+5849 B）

| 位置 | 改动 |
|---|---|
| 类属性 | 新增 `_release_group_keywords`（85 条，与 `metavideov2.py::__fix_release_group` 同一口径） |
| `__init__`（原 84-85 行） | 由「无条件删第 1 个 `[...]`」改为 `while` 循环：**是发布组/字幕组、且删掉后剩下的部分仍有能当片名的 token** 才删（可连续剥多个前导垃圾方括号） |
| 新增方法 | `__is_release_group_token` / `__has_name_like_token` / `__looks_like_name_token` |

### 判据为什么是两条，而不是一条

单看方括号里的内容**无法**区分 `[Oppenheimer]`（片名）和 `[Airota]`（组名）——
两者都是「无空格的纯 ASCII 短串」。所以再加一条**看删除后果**的判据：

> 删掉这一截之后，剩下的部分里还有没有「能当片名」的 token？
> 如果只剩年份 / 分辨率 / 来源 / 编码 / 扩展名（`[Oppenheimer].2023.2160p.WEB-DL.H265.mkv`
> 正是这种），那方括号里装的必然是片名，**不能删**。

`__looks_like_name_token` 复用 `MetaVideo` 自己的正则（`_season_re` / `_episode_re` /
`_resources_pix_re` / `_video_encode_re` / `_audio_encode_re` / `_name_nostring_re`），
并要求 token 含小写字母或中文 —— 把 `DL`、`AAC` 这类编码缩写排除掉。

含中文时另用一张更窄的表（只取关键词表里**含中文**的条目）：否则 `Jade` 这种英文词会命中
关键词表里的 `JADE`（翡翠台），把 `[诛仙 Jade]` 整段删掉。

顺带修好的一类：`[2024][1080p]Movie Title.mkv` —— 原先只剥第一个 `[2024]`，
剩下的 `[1080p]` 仍会在 `__init_name` 里抢先把 `_stop_name_flag` 置真、把后面的片名全挡掉；
现在 `while` 会连续剥干净，能正确得到 `Movie Title`。

## 影响面

- **只动 `MetaVideo`（关闭「增强识别V2」这条分支）**：不改 `metavideov2.py`、不改 `media.py`、
  不改 `metainfo.py`。
- 打开「增强识别V2」时行为**完全不变**。
- 关闭时：不带前导方括号的命名（占绝大多数）**逐项不变**。

## 验证

`artifacts/_verify_bracket_632.py` —— **21 / 21 全绿**（真实 `MetaInfo` / `MetaVideo` /
`MetaVideoV2`，修复前的类从 `git show HEAD:...` 取出做对照）：

| 组 | 内容 | 结果 |
|---|---|---|
| A | 用户上报文件名，关闭 v2 走真实 `MetaInfo` | 路由到 `MetaVideo`；`get_name() == '诛仙'`；类型=电视剧；S02E01；2024；2160p；**不触发 `media.py` 中止** |
| B | 同一文件开 v2 对照 | 走 `MetaVideoV2`，同样得到 `诛仙` |
| C1 | 11 个无前导方括号的常规命名 | 修复前后**逐项一致** 11/11 |
| C2 | 11 个「方括号里是片名」 | 全部识别正确 11/11 |
| C3 | 8 个「方括号里是发布组/字幕组」 | 仍被正确剔除 8/8 |
| D | AST 硬判据 | 已无不条件 `re.sub(_name_no_begin_re, "", title)`；`while` 同时调用两个新方法；关键词表 85 条（含 62 条中文条目） |
| E | 批量健壮性 | 空名从 6 个降到 **0** 个 |

对照探针 `artifacts/_probe_bracket_fix_632.py`（27 个语料 × 现状v1 / 补丁后v1 / v2 三方对照）：
补丁改变结果 12 处、**全部为修复**；空名从 8 个降到 1 个（仅剩 `01.mp4` —— 纯数字命名，
`MetaVideo` 里属专门设计的「纯数字命名」短路分支，v2 同样给不出名字）。

# v6.3.1 — 修复：文件转移只处理了一部分（整批提前中断）

## 现象

「文件管理」页面顶部点「转移」（整理当前目录），列表显示「共 26 个文件」，
实际只有前几个被转移，后面的**一个都没动**，界面也不报错。

## 调用链

```
web/templates/rename/mediafile.html
  「转移」按钮 → mediafile_transfer_all()
             → show_manual_transfer_modal(3, 当前目录, ...)   # manual_type=3 = 自定义识别
             → manual_media_transfer() 提交 cmd="rename_udf"
web/action.py
  __rename_udf → __manual_transfer → FileTransfer().transfer_media(..., udf_flag=True)
app/filetransfer.py
  transfer_media 的 for 循环
```

## 根因：循环体内有 8 处 `return __finish_transfer(...)`

`transfer_media` 的主体是一个

```python
for file_item, media in Medias.items():
    try:
        ...
    except Exception as err:
        log.error(...)
```

循环体里有 **8 处 `return __finish_transfer(...)`**，其中 **7 处带 `if udf_flag:` 前缀**
——「文件管理 → 转移」走的正是 `udf_flag=True` 这条路径，于是：

| 位置 | 触发条件 | 原行为 |
|---|---|---|
| 未识别 | `not media.tmdb_info` 等 | `udf_flag` 时整批 return |
| 目的目录不存在 | `os.path.exists(dist_path)` 为假 | **无条件** return（不分模式） |
| 蓝光原盘目录已存在 | `dir_exist_flag and bluray_disk_dir` | `udf_flag` 时整批 return |
| 覆盖转移失败 | `__transfer_file(...) != 0` | `udf_flag` 时整批 return |
| 拼不出季集目录 | `not ret_dir_path` | `udf_flag` 时整批 return |
| 拼不出集数文件名 | `not ret_file_path` | `udf_flag` 时整批 return |
| 蓝光目录转移失败 | `__transfer_bluray_dir(...) != 0` | `udf_flag` 时整批 return |
| 文件转移失败 | `__transfer_file(...) != 0` | `udf_flag` 时整批 return |

只要其中任意一个文件命中，`__finish_transfer()` 就会把进度条推到 100% 并结束整批，
**后面排队的文件全部不会被遍历到**。

### 实测复现（修复前）

`artifacts/_repro_transfer_batch.py`：26 个真实文件、真实 `MetaInfo`、真实 COPY，
只桩掉 TMDB 网络查询；让第 6 个文件识别不出：

```
返回状态        status  = False
返回消息        message = 无法识别媒体信息
入库历史条数    = 5
目标目录实际文件数 = 5
>>> 判定：整批在中间被【提前中断】
```

第 6 个文件（`S02E06`）一失败，`S02E07`~`S02E26` 全被丢弃。

## 另外两处同类缺陷（一并修掉）

### 1. 识别阶段的静默丢文件

`Media.get_media_info_on_files()` 内部对识别不出的文件是 `continue`：这些文件
**不会出现在 `Medias` 里**，循环根本看不到它们，也不会留下任何记录 ——
界面显示 26 个、实际处理 25 个，用户没有任何线索。

修法：在循环前用 `missed_files = [f for f in file_list if f not in Medias]` 对比出差额，
逐个登记到「未识别」表并计入失败/告警。

### 2. `except` 分支不记账 → 「没成功也没失败」被当成成功

循环体的 `except` 只打日志，不 `failed_count += 1` 也不进 `alert_messages`。
收尾时 `success_flag` 仍是 `True` ⇒ 接口返回成功、界面弹「处理成功」，但文件没转。

修法：`except` 里补记账（`success_flag=False` + `failed_count` + `alert_messages`）。

## 改动清单（1 文件 13 处，`app/filetransfer.py`）

| # | 位置 | 改动 |
|---|---|---|
| M1 | 循环前 | 新增 `success_count`；新增 `missed_files` 记账（识别阶段漏网文件） |
| M2 | 未识别 | 删掉 `if udf_flag: return` |
| M3 | 目的目录不存在 | 由**无条件 return** 改为「记账 + continue」 |
| M4 | 蓝光原盘目录已存在 | 删掉 `if udf_flag: return` |
| M5 | 覆盖转移失败 | 删掉 `if udf_flag: return` |
| M6 | 拼不出季集目录 | 删掉 `if udf_flag: return` |
| M7 | 蓝光目录转移失败 | 删掉 `if udf_flag: return` |
| M8 | 拼不出集数文件名 | 删掉 `if udf_flag: return` |
| M9 | 文件转移失败 | 删掉 `if udf_flag: return` |
| M10 | 转移成功处 | 新增 `success_count += 1` |
| M11 | 收尾 | 汇总「共 N 个 / 成功 X 个 / 未转移 Y 个（原因）」 |
| N1 | `except` | 补记账（异常不再被当成成功） |
| N2 | 收尾 | 未转移数以 `len(file_list) - success_count` 为准 |

## 一个必须小心的边界：不要把「没转移」当成「失败」

`app/downloader/downloader.py` 的下载器监控（每 300s 一轮）里有：

```python
done_flag, done_msg = self.filetransfer.transfer_media(...)
if not done_flag:
    log.warn(...)
    # 失败不登记账本，下轮会重试
    continue
self.__ledger_add(...)      # 登记转移账本，避免下轮重复整理
```

而「目的文件已存在、大小一致」属于**没转移但不算失败**（原代码不置 `success_flag=False`）。
如果收尾用「清单数 − 成功数 > 0」去强行把 `success_flag` 翻成 `False`，
这些任务就会**每 300s 被重复整理一次**。

因此收尾**只改返回消息、不改 `success_flag`**；`success_flag` 仍只由真正的失败点置 `False`
（未识别 / 拼不出季集 / 转移失败 / 异常）。已加场景 E 专门守住这条：
同一批文件连跑两轮，第二轮返回仍为 `True` 且不产生重复入库。

## 验证

- `artifacts/_verify_transfer_batch_631.py` —— **21 / 21 全绿**（真实 `FileTransfer`、
  真实 `MetaInfo`、真实文件复制）：

  | 场景 | 构造 | 断言 |
  |---|---|---|
  | A | 26 个文件，第 6 个识别不出 | 成功 25、未识别记录 1、落盘 25、消息含「成功 25 个 / 未转移 1 个」 |
  | B | 26 个文件，第 6 个文件名拼不出季集 | 成功 25、落盘 25、消息含具体原因 |
  | C | 26 个文件，第 6 个转移动作返回非 0 | 成功 25、落盘 25 |
  | D | 26 个文件全部正常 | 成功 26、落盘 26、返回 `True` |
  | E | 同批文件连跑两轮（第二轮文件均已存在） | 仍返回 `True`、不产生重复入库 |

- **结构判据（AST 级）**：`transfer_media` 的 `for` 循环体内 `Return` 节点数 = **0**，
  从语法层面保证「单文件失败不再中断整批」。
- **接线判据**：`web/action.py` 的 `__rename_udf` 确实以 `udf_flag=True` 调用；
  `mediafile.html` 的「转移」按钮确实走 `manual_type=3`。
- 存量回归（9 个历史脚本）全绿；`py_compile` 通过；AST 顶层 `MatMult` = 0；
  `git ls-files --eol` 仍为 `i/lf`（索引存储未变）。

# v6.3.0 — 重做：种子管理模式（三模式语义 / 参数下发 / 报警 / 界面显隐）

## 背景

「种子管理模式」是 qBittorrent 独有的一组三档设置（默认 / 手动 / 自动），它决定
**「设置 → 下载器 → 下载目录设置」里那些列会被下发给下载器**。旧实现有三处对不上：

| # | 问题 | 代码事实 |
|---|---|---|
| 1 | **模式只在「目录为空」时才生效** | `add_torrent()` 里 `if download_dir: is_auto = False`，一旦拿到非空目录就把 `is_auto` 钉死，后面 `match self._torrent_management` 的三分支**永不执行** ⇒ 「自动」模式在填了「下载保存目录」的行上静默退化成手动 |
| 2 | **报警是结构性误报** | 告警守卫 `if not download_dir:` 只看目录，而分类名要到**晚 7 行**才补上（`if not category: category = download_info.get('category')`）⇒ 分类名已算出来也照样 warn |
| 3 | **「默认」模式并非「什么都不发」** | 只要表里匹配到行，`save_path` 与 `category` 照发；`autoTMM` 还会镜像 qB 的全局设置；`init_torrent_management()` 在该模式下也会去创建 / 覆盖 qB 分类 —— 与「尊重下载器自身设置」相矛盾 |

## 改动（4 文件）

### 1. `app/downloader/client/qbittorrent.py` —— 模式语义收敛成**唯一一个纯函数**

新增模块级 `resolve_torrent_management(mode, save_path, category)`，返回
`{save_path, category, auto_tmm, level, reason}`。参数下发与日志提示**都从这里取结果**，
所以「模式切换 / 参数下发 / 报警」三者不可能各说各话。

| 模式 | `save_path` | `category` | `autoTMM` |
|---|---|---|---|
| `default` | 不传 | 不传 | 不传 |
| `manual` | 传（NAStool 定） | 不传 | `False` |
| `auto` | 有分类 → 不传；无分类 → 传 | 有分类 → 传 | 有分类 → `True`；否则 `False`；全无 → 不传 |

**「不传」= 传 `None`，这是有依据的**：`qbittorrent-api` 把 `data` 里每个值写成
`(占位值, 实际值)` 元组，而 `requests` 编码表单时**会跳过元组里的 `None`**
（`qbittorrent-api` 的 `torrents.py` 里 `"autoTMM": (None, use_auto_torrent_management)`，
`requests._encode_params` 里 `if v is not None`）⇒ 传 `None` 等于**该参数不出现在请求体**，
下载器用它自己的设置。已按 `qbittorrent-api==2023.9.53`（与 `requirements.txt` 同版本）
的真实源码 + 真实编码跑通验证（见下「验证」B 组）。

`add_torrent()` 相应改造：新增 `auto_tmm=None` 形参，由上层传决定好的值；
删掉 `if download_dir: is_auto = False` 的提前钉死与 `match self._torrent_management` 三分支；
**删除 `__check_category()`**（它是第 4 条隐式分支：自动模式无分类时按保存目录反查分类，
新语义下已不可达 —— 无分类时直接下发目录给下载器）。

`init_torrent_management()` 改为**只有 `auto` 模式**才继续往下走，默认与手动模式直接返回：
默认模式不再创建 / 覆盖 qB 分类，手动模式的目录逐任务下发、无需维护分类。
新增 `get_torrent_management()` 供上层读取模式。

### 2. `app/downloader/downloader.py` —— 内部用途与下发用途分开 + 分模式报警

- `downloader_type` 提前到「下载目录设置」之前计算（模式只对 qBittorrent 生效）。
- 新增 `add_dir`（真正下发）与 `auto_tmm`；**表格解析出的 `download_dir` / `container_path`
  仍用于内部**：下载历史登记、站点字幕目录、转移链路的路径映射 —— 默认模式「不下发参数」
  不等于「这张表作废」。
- 策略函数**延迟导入**（在 `if downloader_type == DownloaderType.QB:` 内）：
  `qbittorrent.py` 依赖第三方库 `qbittorrentapi`，而下载器客户端是运行期动态加载的
  （`SubmoduleHelper.import_submodules`，不是安装依赖）。放到模块顶层会让「没装这个库」
  从「某一个下载器不可用」放大成「整个应用起不来」。
- 新增 `__log_torrent_management()`：三种模式只在该说话时说话（默认只 info、绝不 warn）。
- **调用方显式指定下载目录的路径保持原行为**：刷流 / IYUU / 转移 / 订阅都会直接传
  `download_dir=`，那是明确的指令而不是从表格推出来的，因此不走模式 —— 目录照发 +
  强制 `autoTMM=False`（避免下载器按分类把文件搬走）。

### 3. `app/conf/moduleconf.py` —— tooltip 与三模式语义对齐

### 4. `web/templates/setting/downloader.html` —— 按模式显隐填写项

新增 `refresh_dir_mode_ui()`，在「模式切换 / 切换下载器类型 / 弹窗显示 / 新增规则行」四处调用：
默认模式把整张表**折叠**并在表头写明「本表仅用于路径映射与自动分类」；手动模式隐藏
「分类标签」列；自动模式全部展示。**只做显隐，不动取值** —— 隐藏的输入框仍会随表单提交，
切模式不会丢数据。

### 5. `web/templates/setting/downloader.html` —— 附带修复：补上缺失的 `OOPS` import

该模板在「没有下载器」的空状态分支里调用了 `OOPS.empty(...)`，却从未
`{% import 'macro/oops.html' as OOPS %}`。**全仓扫描确认这是唯一一处**（其余 17 个用到
`OOPS.` 的模板都 import 了，见 `artifacts/_scan_oops_import_630.py`）。后果：
**未配置任何下载器时打开「设置 → 下载器」直接 500**（Jinja `UndefinedError: 'OOPS' is undefined`）。
已在模板首部补上该 import，并加了「空状态整页可渲染」的回归断言（验证 E11/E12）。

> 这个缺陷是做本次验证时**渲染整页真实 Jinja 模板**才暴露出来的 —— 说明「真跑真实模板」
> 比只读源码更能发现问题。

## 已知边界（本版刻意不做）

- **手动模式隐藏「分类标签」列**：该列同时是 fork「自动分类」规则的编辑入口（选「自动判定」
  的行即规则）。手动/默认模式下要改这些规则，需切到「自动」模式再改 —— 规则本身照常生效。
- 「种子管理模式」新建下载器时的**默认档仍是「手动」**（原行为，未改动）。
- 非 qBittorrent 下载器（Transmission 等）没有「种子管理模式」这个概念，一律按原样处理。

## 验证

- `_verify_bump_630.py` 全绿（托管 venv：真实 `qbittorrent-api==2023.9.53` + 真实 Jinja）。
- **B 组用真实库跑出结论**：截获 `qbittorrentapi.Client.torrents_add()` 真实发出的 POST
  data，再用真实 `requests` 编码 —— 证明 `autoTMM=None` 时该键**确实不在表单体里**，
  而 `True` / `False` 时在。
- **C 组真跑 `Qbittorrent.add_torrent`**（只把 `qbc` 换成录制器）：三模式参数映射逐一断言。
- **D 组真跑 `__log_torrent_management`**：六种情形逐一断言 info/warn 条数 ——
  其中「默认模式 warn 必须为 0」正是本次修掉的误报。
- **E 组真实 Jinja 渲染整页**，断言新增 id / 函数 / 三处调用点都在渲染结果里。
- **G 组反向注入**：把策略换成「永不下发 autoTMM」、把「手动」分支与「自动按目录回退」分支
  从真实源码里摘掉再执行，断言行为确实改变 —— 证明判据在测这段新代码而不只是同义反复。

# v6.2.9 — 新增：飞牛影视登录态失效自动重登

## 背景

飞牛影视是**登录型**鉴权（`/user/loginByPassword` 取 token，之后每个请求带 `Authorization`），
而登录只在 `TrimeMediaClient.init_config() -> _connect()` 里做**一次**；`init_config()` 只有两个
触发点：**进程启动** 与 **用户登录 NAStool**。于是服务端一旦作废会话，就再也回不来：

| 失效场景 | 后果 |
|---|---|
| NAS / 飞牛影视服务重启 | token 失效 |
| 登录会话过期 | token 失效 |
| 修改飞牛密码 | token 失效 |

而 `__is_ready()` 只检查**本地存的 token 字符串是否非空** —— 服务端早已废弃会话，它照样返回
「就绪」；`request()` 遇到 401 只写日志然后返回失败，**没有「重登再试一次」这条路径**。

症状（会一直持续、不会自愈，只能重启容器）：首页媒体库列表变空、封面不显示、
「媒体库同步」失败或同步出 0 条、入库前的「媒体是否已存在」判断失真（可能重复下载）。

对比：Emby / Jellyfin / Plex 用长期 API Key，不依赖短期会话；绿联影视虽同为登录型，
但登录时带 `keepalive: True` 向服务端申请长会话。**飞牛这两样都没有**。

## 改动（2 文件 / +131 −3）

### 1. `app/mediaserver/client/trimemedia.py`（LF）

**`request()` 新增 `allow_relogin` 参数 + 两个失败出口自愈**：

```python
if not res.ok:
    if allow_relogin and self.__is_auth_error(res.status_code) \
            and self.__auto_relogin():
        return self.request(..., allow_relogin=False)   # 重登成功后重放，且只重放一次
```

响应体 `code` 分支同理（有些网关把 401 塞在 body 里，HTTP 仍是 200）。

**新增 `__auto_relogin()`，三道护栏**：

| 护栏 | 常量 | 默认 | 作用 |
|---|---|---|---|
| 冷却 | `RELOGIN_COOLDOWN` | 30s | 批量同步时几十个请求会同时拿到 401，没有冷却会并发打几十次登录接口 |
| 上限 | `MAX_RELOGIN` | 2 | 连续失败达上限即停止，密码错时不刷日志 |
| 冷静期 | `RELOGIN_RETRY_INTERVAL` | 600s | 停止后隔 10 分钟重开一轮，否则「服务端临时不可用」会让自愈永久失效 |

`__is_auth_error()` 只认 **401 / 403**（HTTP 状态码与响应体业务码通用）。刻意**不猜**
「哪个业务错误码代表未登录」—— 飞牛未登录时返回什么业务码没有可靠依据，乱猜会把正常业务
错误也拿去重登。日后实测到其它未登录码，加进 `AUTH_ERROR_CODES` 即可。

**`login()` / `logout()` 内部的请求一律传 `allow_relogin=False`** —— 否则登录接口自身 401 时会递归重登。

**⚠️ 一个必须踩对的细节：重登失败要把原 token 放回去。**

最初的写法是 `self._token = None` 后再尝试重登。这会踩雷：`__is_ready()` 读的正是这个 token，
置空后它恒为 `False`，而 `TrimeMediaClient` 的 20+ 个方法**第一句就是
`if not self.__is_ready(): return`** —— 连请求都不发，自愈从此再没有机会触发，
连「服务端已经恢复」都不知道。故失败路径必须恢复原值：

```python
old_token = self._token
self._token = None
if self.login(...):
    ...
self._token = old_token      # ← 失败也要放回去
```

**新增 `_norm_int()`**（与 `_norm_bool` 同族）：表单提交一律是字符串，`relogin` 读出来可能是
`"3"` / `""` / `"abc"`，统一归一化并夹在 [0, 10]，非法值回落默认值，**绝不抛异常**
（该函数跑在客户端初始化路径上，抛异常会让整个客户端建不起来）。

### 2. `app/conf/moduleconf.py`（CRLF）

`MEDIASERVER_CONF["trimemedia"]["config"]` 新增字段：

```python
"relogin": {
    "id": "trimemedia.relogin",
    "title": "自动重新登录次数",
    "type": "text",
    "placeholder": "2"
},
```

**不需要改任何模板**：设置页表单由 `macro/form.html::gen_form_config_elements` 按该字典
**动态渲染**，前端 `input_select_GetVal()` 按 input 的 `id` 自动收集 —— 加一个字段就够了。
这与「下载目录设置」那类需要手写控件、且**控件放错容器就永远存不上**的表单完全不同。

## 已知边界（本版刻意不做）

`__is_ready()` 为 False（即**从来没连上过**，例如容器启动时飞牛还没起来）时不走本机制 ——
那时连 token 都没有，没有凭据可重登。这属于「断线重连」，入口是 `_api is None`，
与本版覆盖的「连过、之后失效」是两条不同路径，留待后续。

## 验证

- `_verify_bump_629.py` **65/65 通过**（用托管 venv 跑）。
- 判据全部从真实源码派生：`log` 桩的公开面从真实 `log.py` 的 AST 抽取并**双向断言**
  （桩 ⊆ 真实、产品代码引用 ⊆ 真实）；产品代码传给 `requests.Session.request` 的 kwargs
  必须 ⊆ 真实签名（防「桩比真实更宽容」）；配置项从真实 `moduleconf.py` 取，
  并用**真实 Jinja 模板**渲染（含整页 `setting/mediaserver.html`）。
- 真跑 `_TrimeApi`（只把 HTTP 会话换成假 handler），覆盖 11 组场景：401 自愈重放、
  重放仍 401 不递归、登录接口自身 401 不递归、30s 冷却下连续 5 个请求只重登 1 次、
  连续失败达上限停止、冷静期后重开一轮、响应体 `code=401`、HTTP 500 不动重登、
  `max_relogin=0` 关闭、`logout` 不重登、重登失败后 token 被恢复。
- **3 条反向注入**：`__is_auth_error` 边界值（0 / None / `"abc"`）；把自愈代码从源码里
  删掉后 401 **不再**重登（证明判据确实在测这段新代码）；行尾与装饰器粘连
  （AST 顶层 `MatMult`）体检。

# v6.2.8 — 修复：媒体服务器图标改走配置（绿联影视图标 404）

## 背景

「媒体库同步」弹窗的图标一直是**按内部代号硬拼文件名**的：

```jinja
style="background-image: url('../static/img/mediaserver/{{ MediaServerType }}.png')"
```

emby / jellyfin / plex / trimemedia 恰好都有同名 `.png`，所以问题一直没暴露；
但**绿联影视复用的是 emby.png**：

| 类型 | 配置里的 `img_url` | 硬拼出的文件 | 磁盘是否存在 |
|---|---|---|---|
| emby | `.../emby.png` | `emby.png` | ✓ |
| jellyfin | `.../jellyfin.jpg` | `jellyfin.png` | ✓（侥幸，且与设置页图标不一致） |
| plex | `.../plex.png` | `plex.png` | ✓ |
| **ugreen** | **`.../emby.png`** | **`ugreen.png`** | **✗ 不存在 ⇒ 404 空白** |
| trimemedia | `.../trimemedia.png` | `trimemedia.png` | ✓ |

## 根因

同一个语义（「这台媒体服务器的图标」）在**两处用了两套取法**：
设置页 `setting/mediaserver.html:22` 取 `MediaServer.img_url`（读配置，正确），
首页弹窗按 `MediaServerType` 拼文件名（猜文件名，错）。

## 改动（2 文件 / +7 −1）

1. `web/main.py::index()` 新增模板变量 `MediaServerImg`，与已有 `MediaServerName` 同族：

```python
MediaServerImg = (
    (ModuleConf.MEDIASERVER_CONF.get(str(MSType).lower()) or {}).get("img_url")
    or f"../static/img/mediaserver/{MSType}.png"
)
```

   末尾的 `or ...` 是**兜底**：配置里查不到该类型时，行为与改前**完全一致**（仍按代号拼），
   不引入新分支。

2. `web/templates/index.html`：图标表达式改为 `url('{{ MediaServerImg }}')`。

   前缀 `../static/img/mediaserver/` 与改前逐字符相同 ⇒ **相对路径解析基准不变**，
   未新增 / 删除任何静态资源。

## 已知副作用（正向）

Jellyfin 的弹窗图标从 `jellyfin.png` 变为 `jellyfin.jpg`（配置里本就是这个），
结果与设置页**看齐**，两处图标从此一致。

## 刻意未改

`web/main.py` 仍照旧传 `MediaServerType=MSType`。模板已不再引用它，但保留传参属
**零风险保留**（避免影响任何潜在的其它引用）。

## 验证

- `_verify_bump_628.py` 全部通过。判据全部从**真实源码派生**：`MEDIASERVER_CONF` 用
  `ast.literal_eval` 从 `moduleconf.py` 抽真字典，`MediaServerImg` 表达式用
  `ast.get_source_segment` 从 `main.py` 抽真实源码，模板行为用**真实 Jinja 渲染**比对。
- **新增一条磁盘守卫**：断言 5 种类型配置的 `img_url` 指向的文件**必须真实存在**
  —— 防的正是「将来再加媒体服务器时又指向不存在的文件」这类复发。
- 含 **4 条反向注入**（旧表达式复现 `ugreen.png` / 磁盘守卫报红 / 变量回退 / 模板回退）。
- 存量回归：飞牛全套 + 媒体同步 + 排版守卫全绿。

## 附带改动：发布说明标题去掉「（MoviePilot 风格）」

`CHANGELOG.md` 第 1 行就是 GitHub Release 的正文标题 —— CI `.github/workflows/build.yml`
用 `body_path: CHANGELOG.md` 把**整份文件**作为 Release body，所以那行会出现在
**每个** Release 页面的最顶部。本次一并改为 `# 发布说明`，历史 Release 的正文也通过
API 同步清理。

正文里**技术性**的 MoviePilot 引用（「参照上游实现」「报文逐字节对齐」这类溯源说明）
一概保留 —— 那是实现依据，与标题措辞是两回事。

# v6.2.7 — 补齐：同步弹窗标题也走显示名

## 背景

v6.2.6 把首页标题从 `MediaServerType`（内部代号）换成了 `MediaServerName`（显示名），
但**只改了页面标题那一处**。用户实测发现，点「媒体库同步」弹出的那个完成弹窗顶部
仍写着 `Trimemedia` —— 那是同一份模板里的另一处引用，v6.2.6 漏掉了。

## 根因

`web/templates/index.html` 里，同一个模板变量被用于两层不同语义：

| 用途 | 变量 | 为什么不能互换 |
|---|---|---|
| 静态图标文件名 | `MediaServerType` | 文件名就是内部代号（`trimemedia.png`），换中文名会 404 |
| 给人看的名字 | `MediaServerName` | 取 `ModuleConf.MEDIASERVER_CONF[type]["name"]`，已是中文名 |

第 193 行用 `MediaServerType` 拼图标路径（**正确，不动**）；第 195 行原本也用了
`MediaServerType`，但套了个 `|title` 过滤器 —— 它把 `trimemedia` 变成 `Trimemedia`，
看着像个正经人名，所以从界面上一眼看不出来。

⚠️ `web/main.py` 里另有 `MediaServerType.PLEX` / `.JELLYFIN` / `.EMBY` 这类引用，
那是 `app.utils.types.MediaServerType` **枚举**（值本身已是 `Plex` / `绿联影视` / `飞牛影视`），
与模板变量同名但完全无关，**不能一起改**。本次锚点核对专门把这三行排除在外。

## 改动（1 文件 / 1 行）

| 文件 | 改动 |
|---|---|
| `web/templates/index.html` | 第 195 行 `{{ MediaServerType\|title }}` → `{{ MediaServerName }}` |

`git diff --stat` = `1 file changed, 1 insertion(+), 1 deletion(-)`；行尾保持 CRLF
（补丁走「归一成 LF 替换 → 按原行尾写回」，并断言 CRLF/裸 LF 计数不变）。

## 影响面

| 媒体服务器 | 改前显示 | 改后显示 |
|---|---|---|
| 飞牛影视 | `Trimemedia` | `飞牛影视` |
| 绿联影视 | `Ugreen` | `绿联影视` |
| Emby / Jellyfin / Plex | `Emby` / `Jellyfin` / `Plex` | 不变（显示名与代号仅大小写之差） |

## 测试

- `_verify_bump_627.py` 共 **67 项**，含 6 条反向注入，确保判据不是「永远为真」。
- 存量回归：全套通过（trimemedia / fix / regression / runtime / e2e / realhttp /
  diagnose / log 守卫 / bump_626 等）。
- **判据按「行为」而非「字面」**：断言的是「模板里交给用户看的那处用 `MediaServerName`」
  且「图标那处仍用 `MediaServerType`」—— 否则会把本来正确的图标表达式误判成漏改
  （v6.0.3 踩过同类坑：文档里说明「某字段已移除」必然要写出该字段名，导致「出现即失败」的
  扫描判据在发版后必红）。

# v6.2.6 — 图片鉴权闭环：库封面 / 观看记录封面 + 标题显示名

## 背景

v6.2.5 收口了「连不上」的可观测性。用户改对地址后飞牛影视接通，随即暴露两个新问题：

1. 首页标题把内部代号直接写在了页面上：`我的媒体库 - trimemedia`；
2. 「我的媒体库」页里的媒体库封面、以及「正在观看」的封面**全部不显示**。

## 根因（对照上游 MoviePilot 源码确认）

飞牛的图片接口（`/api/v1/sys/img/...`）与业务接口一样校验登录态，**并且需要把登录 token
以 Cookie（`Trim-MC-token`）形式携带**；而 `<img>` 标签无法附加请求头。上游
`app/modules/trimemedia/trimemedia.py::get_image_cookies()` 正是这个口径，且自带同源校验：

```python
if not image_url or not SecurityUtils.is_safe_url(image_url, [self._api.host], strict=True):
    return None
cookies = {"Trim-MC-token": self._api.token}
if self._access_code:
    cookies.update(self._api.cookies)
```

本仓此前**整条图片链路不带任何凭证**，且媒体库封面返回的是飞牛的绝对地址、由浏览器直连
⇒ 100% 失败。

## 改动（6 文件）

| 文件 | 改动 |
|---|---|
| `app/mediaserver/client/_base.py` | 新增**非抽象**钩子 `get_image_cookies(image_url)`，默认 `None` |
| `app/mediaserver/client/trimemedia.py` | 新增 `_TrimeApi.cookies` 属性；实现 `get_image_cookies()`；`get_libraries()` 的 `image` / `image_list` 改走 `get_nt_image_url()` 本机中转 |
| `web/backend/web_utils.py` | `request_cache(url, cookies=None)`；凭证指纹进 `lru_cache` 键；新增 `get_image_cookies(url)` |
| `web/main.py::Img()` | 取凭证并传给 `request_cache`；Etag 含凭证指纹 |
| `web/templates/index.html` | 标题改用 `MediaServerName` |
| `scripts/diagnose_trimemedia.py` | 新增 `probe_image_auth()` 封面鉴权探测 |

## 关键设计

- **同源白名单（安全红线）**：`get_image_cookies` 只在图片地址与本机配置的 `host` 同源
  （scheme + host + port 完全一致）时才返回凭证。否则 `/img?url=<任意地址>` 会变成
  「带着 token 请求任意 URL」的凭证外泄 / SSRF 通道 —— 比不做更危险。实测拦下
  `userinfo 伪装`（`http://ip:port@evil.com`）与「把目标地址塞进 query」两种写法。
- **钩子必须非抽象、默认 `None`**：写成 `@abstractmethod` 会逼 Emby / Jellyfin / Plex / 绿联
  五个已有客户端全部实现；默认返回 `None` ⇒ 老客户端行为完全不变。
- **`lru_cache` 键必须含凭证指纹**：`dict` 不可哈希 ⇒ 转成 `tuple(sorted(...))`；
  否则「换了凭证仍命中旧缓存」，表现为登录态变了图片却还是旧的。
- **登录 token 权威**：先合并会话 Cookie、最后写 `Trim-MC-token`。上游顺序相反，
  若会话里恰好存在同名 Cookie 会把登录 token 顶掉。

## 离线验证逮到的两个真 bug

1. **`request_cache` 的 douban 分支会丢凭证**：原代码 `if url.find('douban'):` 缺 `!= -1`，
   实际几乎总走这一支，而这一支调用 `RequestUtils(referer=...)` **没带 cookies**
   ⇒ 即便实现了凭证注入，图片依然取不到。已在两支都带上。
2. **`_split_origin` 的宽 `except Exception` 会把编程错误吞成「不同源」**：测试命名空间漏了
   `urlsplit` 时它静默返回 `None`（= 白名单永久不通过），表现为「功能不生效但不报错」。
   已收窄为 `except ValueError`，只吞「地址本身畸形」。

## 测试

- `_verify_bump_626.py` **65 项全绿**，含 4 条反向注入（标题回退 / 钩子加 `@abstractmethod` /
  摘掉同源校验 / 缓存键去掉凭证指纹），确保判据不是「永远为真」。
- 存量回归：trimemedia 26 / fix 63 / regression 31 / runtime 20 / e2e 36 / realhttp 59 /
  diagnose 6 / log 守卫 7 / bump_625 48，全部通过。
- 两处**过期判据**随行为变化更新（行为是刻意改的）：
  `_verify_trimemedia_fix.py` 的「库封面是绝对地址」→「库封面走本机 /img 中转」；
  `_verify_diagnose_script.py` 的服务端验签**豁免图片请求**（`<img>` 式请求按设计不带 authx）。

# v6.2.5 — 连接诊断闭环：首页给原因 + 诊断脚本前置体检 + 保存后回读

## 背景

v6.2.3 / v6.2.4 解决的是「配置没落盘」与「报错被覆盖」两个具体缺陷。但真机上仍可能出现
「测试失败 / 首页连接失败」而**信息不足**：报错文案写死的是 Emby/Jellyfin/Plex、且不给原因；
诊断脚本只有接口层，一旦 DNS / 端口 / 证书 / 反向代理这条链断了，它给出的仍然只是一句「请求异常」。

本版把这三处补上，目标是**把「猜」换成「读」**。不涉及任何协议改动，零新依赖。

## 一、首页报错文案：动态名称 + 真实原因

| 文件 | 改动 |
|---|---|
| `web/action.py::get_library_mediacount` | 失败时优先读客户端的 `last_error`，拼成 `媒体库服务器连接失败：<原因>` |
| `web/main.py::index` | 新增 `ServerError` / `MediaServerName` 两个模板上下文 |
| `web/templates/index.html` | 报错文案改为由上下文的名称与原因拼装 |

显示名取自 `ModuleConf.MEDIASERVER_CONF[<type>]["name"]`，即「飞牛影视 / 绿联影视 / Emby …」，
与「设置 → 媒体服务器」卡片上写的一致。

`last_error` 只有实现了该属性的客户端（飞牛影视）才有，用 `getattr(client, "last_error", None)`
读取；Emby / Jellyfin / Plex / 绿联影视 走原路径，文案与以前完全一致（已验证）。

## 二、诊断脚本：四段前置体检

`scripts/diagnose_trimemedia.py` 新增 `preflight()`，在每个候选地址做接口探测**之前**执行：

| 段落 | 判据 | 失败时的提示 |
|---|---|---|
| DNS | `socket.getaddrinfo` | 改用 IP；或给容器配 DNS / 写 hosts |
| TCP | `socket.create_connection(timeout=5)` + 计时 | 端口、服务、容器网络、防火墙 |
| TLS | 先不校验证书取 `getpeercert()`，再单独跑一次真校验 | 自签名 / 主机名不匹配 ⇒ 关「校验SSL证书」 |
| 反向代理 | 不带签名裸探 `{根}/` 与 `{根}/v/`，看 Server 头 / 3xx / 5xx | 重定向 = 网关页；502/504 = 后端不可达；401/403 = 网关鉴权 |

体检不通过就**跳过接口探测**（省掉 10s 超时等待），新增 `--no-preflight` 可关闭。

## 三、保存后回读

`config.py` 新增 `read_config_file()`（从磁盘重读，不改变内存配置）；
`web/action.py::__update_config`：

1. `save_config` 包 `try/except`，写失败返回 `{"code": 1, "msg": "配置文件写入失败：…"}`（此前会 500）；
2. 写成功后调用 `__verify_config_saved(cfgs)` 逐键回读。

**误报控制**（本节最需要小心的地方）：只校验「提交了非空标量值」的键，且只在磁盘上
「找不到 / 是 None / 是空串」时才报；值不同但都存在则不报（后端有密码散列、代理包装等正常转换）。
`app.login_password` 与 `app.proxies` 直接跳过。回读结果不是 dict、或读取抛异常，都只给提示不抛。
返回 `{"code": 0, "msg": ...}` —— `code` 语义不变，其它调用方不受影响。

前端 `mediaserver.html` 在「保存」与「测试」两条回调里都会把 `msg` 弹出来。

## 四、验证

`_verify_bump_625.py`：**48 项断言全绿**。被测逻辑一律用 AST 从真实文件抽源码来跑，
只有 `Config` / `MediaServer` / `log` 等外部依赖做桩。

踩到的两个**测试自身**的坑（已记入技能）：

- `MediaServer.server` 是 `@property`，桩若写成 `@staticmethod`，`instance.server` 拿到的是
  **函数对象**而不是客户端实例 ⇒ 测试假红。这是「桩必须从真实模块派生」的又一变体。
- `ruamel.yaml` 要用 `import ruamel.yaml`，只 `__import__("ruamel")` 拿不到 `.yaml` 属性 ⇒ 测试假红。

## 五、附带修正：log API 守卫的假阳性

`_verify_log_api_usage.py` 的 [B] 段原先用文本正则找 `for n in ("info", ...)`，而
`_patch_bump_624.py` 的发布说明里**贴了一段含 `"warning"` 的示例代码**，被误判成「桩造了不存在的名字」。
现改为 AST 识别（`ast.For` + `ast.Tuple` + 邻近 `setattr(`），并加两条反向注入：
真桩要能识别、贴在文档字符串里的同款样例不能识别。

## 六、其它

- `_check_anchors_625_bump.py`（只读锚点核对）在本版实测拦下一个错锚点：
  `index.html` 里 `MediaServerName` 我写 want=3，实际是同一行出现 2 次。
- 灰度结论：本版**不承诺「改完就能连上」**。「连不上」的成因可能在地址 / 网络 / 反代侧，
  本版的作用是把它**指出来**。协议层报文与上游 MoviePilot 的比对仍是 40/40 一致。

# v6.2.4 — 修复 log.warning 越界调用 + 加固测试桩

## 用户可见的问题

飞牛影视点「测试连接」弹窗：

```
测试失败：
地址探测异常（http://192.168.3.3:5666）：AttributeError: module 'log' has no attribute 'warning'
```

## 根因

仓库根 `log.py` 只提供 `debug / info / error / warn / refresh_loglevel / console`，
**没有 `warning`**（这个项目里叫 `warn`）。

而 v6.2.3 新增的 `_resolve_api()` 诊断日志里写了 `log.warning(...)`：

```python
detail = api.last_error or "版本接口未通过校验"
log.warning(f"【…】地址探测失败（{cand}）：{detail}")   # ← 这一行自己抛 AttributeError
```

**为什么杀伤力被放大**：该行位于 `try` 内部，下面紧跟 `except Exception` ——
它自己抛的 `AttributeError` 被**当成「探测失败的原因」记了下来**，
把 `api.last_error` 里**真正**的原因（HTTP 状态码 / 返回非 JSON / 连接被拒）覆盖掉了。
两个候选地址走完，用户能看到的就只剩这句 AttributeError。

同类越界调用共 2 处：
- `_resolve_api()` 里这处 —— 会覆盖真实原因，**危害大**；
- `_connect()` 里「播放地址不可达」那处 —— 会直接中断 `init_config`。

## 修复

1. `log.warning(` → `log.warn(`（2 处）。
2. **候选地址失败原因改为累积**：原先 `last_detail` 会被后一个候选覆盖，
   现在用 `reasons` 列表把每个候选的原因都收起来，最终 `last_error` 形如
   `所有候选地址均无法连接；http://ip:5666/v → HTTP 404…；http://ip:5666 → …`。

## 为什么 330 项断言没拦住（真正的教训）

`_verify_*` 系列脚本里的 `log` 桩是**手写**的：

```python
lg = types.ModuleType("log")
for n in ("info", "error", "warn", "warning", "debug"):   # ← 我凭空造了 "warning"
    setattr(lg, n, lambda *a, **k: None)
```

**桩比真实实现更宽容** ⇒ 产品代码里不存在的调用也照样「通过」。
这与 v6.2.0「132 项全绿却真机必挂」是**同一类错误**，只是换了个模块。

### 新增守卫 `_verify_log_api_usage.py`

双向设防，全部**从真实源码派生**、不手写：

| 段 | 内容 |
|---|---|
| A | 解析 `log.py` AST 得到真实公开面；扫描第一方代码里所有 `log.<name>` 调用，越界即失败 |
| B | 扫描所有验证脚本的 `log` 桩名字表，**桩里出现 `log.py` 没有的名字即失败** |
| C | 把 `trimemedia.py` 依赖的 `ExceptionUtils` / `MediaType` / `MediaServerType` 也按真实成员校验 |

[A] 实跑：`log.warn` 146 次、`log.info` 423 次、`log.error` 345 次…全部合法，越界 0。
[B] 实跑：**10 个脚本**（不只本次相关的）都把 `"warning"` 写进了桩 —— 已用
`_fix_logstubs.py` 按真实公开面统一矫正，并在每处加注释指回守卫。

**反向测试**（证明守卫有牙）：临时写入一个含 `import log` + `log.warning("x")` 的文件，
守卫立即报「产品代码使用了 log.warning（1 处，首处 app\_tmp_logcheck.py:5）」✓，随后删除。

## 改动文件

| 文件 | 变更 |
|---|---|
| `app/mediaserver/client/trimemedia.py` | `log.warning` → `log.warn`（2 处）；`_resolve_api` 累积每个候选地址的失败原因 |

## 测试

| 脚本 | 项数 | 结果 |
|---|---|---|
| `_verify_log_api_usage.py`（**新增守卫**） | 5 | ✓ |
| `_verify_tri_vs_upstream.py` | 40 | ✓ |
| `_verify_trimemedia_diagnostics.py` | 28 | ✓ |
| `_verify_diagnose_script.py` | 6 | ✓ |
| `_verify_trimemedia_realhttp.py` | 59 | ✓ |
| `_verify_trimemedia_fix.py` | 63 | ✓ |
| `_verify_trimemedia_e2e.py` | 36 | ✓ |
| `_verify_trimemedia_regression.py` | 31 | ✓ |
| `_verify_trimemedia.py` | 26 | ✓ |
| `_verify_trimemedia_runtime.py` | 20 | ✓ |

合计 **314 项断言，0 失败**，且本次全部使用**已校正的严格桩**。

---

# v6.2.3 — 连接诊断与「测试即保存」

## 背景

用户反馈「测试也是失败」，并质疑「你怎么测试的」。核对后确认两点：

1. **代码协议层没有问题** —— 本版补了「与上游逐字节比对」的验证（见下），40 项全一致；
2. **接入层确实有两个坑**：点「测试」不保存配置；失败原因被吞掉。两者叠加，用户看到的
   就是「测试失败 / 首页媒体服务器连接失败 / 媒体库列表 0/0」，而且完全无从下手。

## 坑 1：点「测试」= 配置从不落盘

`web/action.py::__update_config`：

```python
config_test = False
for key, value in cfgs:
    if key == "test" and value:
        config_test = True
        continue
    cfg = self.set_config_value(cfg, key, value)   # 就地改 Config()._config
if not config_test:
    Config().save_config(cfg)                      # ← 只有非测试分支才落盘
```

而 `web/templates/setting/mediaserver.html::test_mediaserver_config` 传的正是 `test=true`：

```js
save_config(type, function (ret) { ... }, true);   // ← 第三个参数就是 test
```

`set_config_value` 是**就地修改** `Config()._config` 的，所以：

- 点「测试」→ 值进内存 → 本次测试能用到新值 ✓
- **但从不 `save_config()` 落盘** → 容器一重启，配置回滚到上一次保存的状态 ⇒ 全空
- 用户随后看到的「首页媒体服务器连接失败」「媒体库列表 0/0」正是配置为空的直接后果

**修复**：`test_mediaserver_config` 改为**先保存、再测试**（去掉 `test=true`）；
`save_config` 也只在真的需要「仅测试」时才带 `test` 字段（原先无条件写 `test: false`，
会把这个无用字段顺手写进配置文件）。

## 坑 2：失败原因被吞掉

`__test_connection` 原先只返回 `{"code": 0/1}`，前端只能显示「测试失败！」。
一个连不上飞牛的用户，拿不到任何可行动的线索。

**修复**：新增 `last_error` 字段，把最底层的原因一路带上来。

| 层 | 内容 |
|---|---|
| `_TrimeApi.last_error` | `HTTP 404（url）Content-Type：... 响应：...` / `返回非 JSON 响应（HTTP 200，url）...` / `错误码 1001：用户名或密码错误（url）` / `ConnectionError: ...（url）` |
| `TrimeMediaClient.last_error` | `未读到「飞牛影视」配置：请先…` / `配置不完整，缺少：用户名、密码` / `登录失败：错误码 1001…` / `获取用户信息失败：…` |
| `__test_connection` | 读 `last_error` 放进返回的 `msg`；**导入/构造类异常也回显**（此前被静默吞成「测试失败」） |
| `mediaserver.html` | 失败时 `alert` 弹出原因 |

## 坑 3：请求报文与上游有 2 处细节差异

与 MoviePilot 官方 `api.py` 逐字节比对后发现：

| # | 差异 | 处理 |
|---|---|---|
| 1 | POST 且无请求体时：上游发 `""` 并带 `Content-Type: application/json`，本仓发 `None` 且不带 | 对齐上游 |
| 2 | `json.dumps` 上游带 `allow_nan=False`，本仓带 `ensure_ascii=False` | 对齐上游（`ensure_ascii` 只影响中文转义、不影响签名自洽，但一致更稳） |

## 改动文件

| 文件 | 变更 |
|---|---|
| `app/mediaserver/client/trimemedia.py` | `last_error` 全链路；`request()` 补 HTTP 状态码/Content-Type/响应片段；POST 空体与 Content-Type 对齐上游；`_resolve_api` 逐候选记日志并带出底层原因 |
| `web/action.py` | `__test_connection` 回显 `last_error` 与异常 |
| `web/templates/setting/mediaserver.html` | 测试按钮改为「先保存再测试」；失败弹窗显示原因；去掉 `test:false` 脏写 |
| `scripts/diagnose_trimemedia.py` | **新增**：独立诊断脚本（不依赖 NAStool 代码，容器内可直接跑） |

## 测试

| 层级 | 脚本 | 项数 | 结果 |
|---|---|---|---|
| **报文级交叉验证（新增）** | `_verify_tri_vs_upstream.py` | **40** | ✓ 与 MoviePilot 上游 api.py 逐字节一致 |
| **失败原因链路（新增）** | `_verify_trimemedia_diagnostics.py` | **28** | ✓ |
| **诊断脚本（新增）** | `_verify_diagnose_script.py` | **6** | ✓ 四个分支 + 服务端侧独立复算 authx |
| 真实 HTTP 链路 | `_verify_trimemedia_realhttp.py` | 59 | ✓ |
| 修复专项 | `_verify_trimemedia_fix.py` | 63 | ✓ |
| 端到端 | `_verify_trimemedia_e2e.py` | 36 | ✓ |
| 存量回归 | `_verify_trimemedia_regression.py` | 31 | ✓ |
| 声明 / 运行时 / 类型 / 契约 / 缺口审计 | 5 个脚本 | 26+20+21+契约+无缺口 | ✓ |

合计 **330 项断言，0 失败**。

## 为什么这次的验证可信（回应「你怎么测试的」）

此前 v6.2.0 的验证是「自己写假服务端 + 自己写桩」，而**桩比真实实现更宽容**，
形成自证循环，所以 132 项全绿却真机必挂。

本版新增的三件事专门用来打破自证：

1. **`_verify_tri_vs_upstream.py`**：把 MoviePilot 官方 `api.py` 从 GitHub 拉下来，与本仓实现
   在**相同输入**下驱动，固定 `random` / `time` 后逐字节比对实际发出的
   `method / url / params / body / headers / authx` —— **上游代码是协议的唯一权威参照**。
2. **`_verify_diagnose_script.py`**：起本地假服务端，**服务端侧独立复算 authx**
   （不复用被测算法的任何代码），签名对不上直接返回错误码。
3. **`scripts/diagnose_trimemedia.py`**：交付给用户在**真实环境**跑，不依赖 NAStool 任何代码 ——
   它的输出就是真机的第一手证据。

---

# v6.2.2 — 飞牛影视测试连接修复

## 背景

用户反馈「飞牛影视测试一直失败」。v6.2.0 / v6.2.1 的功能只在**本地假服务端**验证过，
这是该客户端第一次与真实服务端联调。

## 根因：HTTP 层调错 API，每个请求必然抛错

`trimemedia.py` 的请求层写的是：

```python
RequestUtils(verify=self._ssl_verify, session=True)
```

而 nas-tools 真实的 `RequestUtils.__init__` 签名是：

```
(headers, cookies, api_key, proxies, session, timeout, referer, content_type, accept_type)
```

- **根本没有 `verify` 参数** ⇒ `TypeError: unexpected keyword argument 'verify'`；
- `session` 要的是 `requests.Session` **实例**，传 `True` 会在内部变成 `True.get(...)` ⇒ `AttributeError`。

于是链路必然断在第一步：

```
_resolve_api() → verify_access_code() / sys_version() 恒失败 → _api = None
→ get_status() 恒 False → 前端永远显示「测试失败」
```

**100% 复现，不可能偶尔通。**

## 为什么 132 项验证全绿仍然漏掉：桩自证循环

v6.2.0 的假服务端验证脚本里的 `_RequestUtils` 是**按记忆手写的桩**，
其签名恰好写成 `(headers, timeout, verify=True, session=False, ...)` ——
**正好接受了我写错的参数**。于是「写错的调用」与「过于宽容的桩」互相印证，形成自证循环。

**教训**：**验证用的桩不能比真实实现更宽容**。参数合法性应当用 `inspect.signature`
对着**真实源码**断言，而不是靠桩「能跑通」来证明。

## 修复

| # | 内容 |
|---|---|
| 1 | 改用真实 `requests.Session`（与 ugreen 客户端同构）+ User-Agent。**副产品**：`ssl_verify` 配置从此真正生效（`RequestUtils` 内部把 `verify` 硬编码成了 False，用它等于一个死开关） |
| 2 | 图片前缀**幂等** —— 服务端若已返回带 `/api/v1/sys/img` 的地址，不再重复叠加 |
| 3 | `_play_host` 未填时不再回落成 `_host`（缺 `/v`，播放链接 404），改为回落到已含 `/v` 的 API 地址 |
| 4 | `get_status()` 补失败原因日志（未配置地址 / 缺用户名密码 / 未建立连接），便于用户自查 |

## 改动文件

| 文件 | 变更 |
|---|---|
| `app/mediaserver/client/trimemedia.py` | 请求层改真实 `requests.Session`；图片前缀幂等；`play_host` 回落修正；`get_status` 补日志 |

合计 1 文件 +66 / −19。

## 测试

| 层级 | 脚本 | 项数 | 结果 |
|---|---|---|---|
| 抽象方法契约 | `_verify_trimemedia_contract.py` | 19 | ✓ |
| 声明 | `_verify_trimemedia.py` | 26 | ✓ |
| 运行时 | `_verify_trimemedia_runtime.py` | 20 | ✓ |
| **真实 HTTP 链路（新增，HTTP 层不桩）** | `_verify_trimemedia_realhttp.py` | **59** | ✓ 43 个 `/v` 请求全部通过服务端侧 authx 复算 |
| 端到端 | `_verify_trimemedia_e2e.py` | 36 | ✓ |
| 修复专项 | `_verify_trimemedia_fix.py` | 63 | ✓ |
| 存量回归 | `_verify_trimemedia_regression.py` | 31 | ✓ |
| 类型判定 | `_verify_mediasync_type.py` | 21 | ✓ |

合计 **275 项断言，0 失败**。

新脚本 `_verify_trimemedia_realhttp.py` 为三段式：

- **A** 从真实源码取 `inspect.signature`，AST 扫描全部媒体服务器客户端的请求构造，参数非法即红；
- **B** 把旧写法喂给真实实现，断言它**确实抛错**（根因确证，反向复现）；
- **C** 真实 `requests` 打假服务端，服务端侧独立复算 authx。

这条护栏对存量客户端同样生效。

## 踩坑

- **验证脚本的桩不能比真实实现宽容** —— 本次根因，详见上节。
- **测试连接用的是「已保存」的配置**：前端点测试时带 `test=true`，后端**不落盘**
  ⇒ 顺序必须是**先「保存」再「测试」**，改了表单直接点测试，测的是上一次保存的配置。
- **真实环境联调前，所有验证都只是「协议自洽」**，不能替代与真机对接。本次就是靠用户实测才暴露。

## 兼容性

- 仅 HTTP 层实现变更，无接口变更、无新依赖、无数据迁移；
- 存量 emby / jellyfin / plex / ugreen **不受影响**（它们各有自己的请求方式，未共用本次改动）；
- `ssl_verify` 在本客户端从「无效」变为「有效」，属**修正**而非行为破坏。

---

# v6.2.1 — 飞牛影视首页数据与图片链路修复

## 背景

v6.2.0 接入飞牛影视后，用户反馈「媒体库同步能跑了，但『我的媒体库』里的内容没变化」。
排查发现这不是同步的问题 —— **「媒体库同步」与首页「我的媒体库」是两条互不相干的链路**：

- 「媒体库同步」把条目写进 `media.db`，**只服务于**「该媒体是否已存在」的判断（搜索结果、详情页标记）；
- 首页「我的媒体库」的「正在观看」/「最新入库」/ 库卡片是**每次打开页面实时**从媒体服务器拉的。

真正的缺陷是首页这条链路上的三个问题。

## 缺陷 1：首页直接 500（缺 2 个方法 + 1 个转发契约）

`web/main.py:297/300` 分别调用 `MediaServer().get_resume()` 与 `MediaServer().get_latest()`，
但这两个方法**不在** `_IMediaClient` 的 19 个抽象方法里，`_base.py` 也**没有默认实现**，
v6.2.0 的飞牛客户端因此漏掉它们 ⇒ 调用直接 `AttributeError`，而那两行**没有 try 兜底** ⇒ **整个首页 500**。

另 `media_server.py` 会转发 `get_episode_image_by_id()`，同样缺失。

**修复**（新增 3 个方法）：

- `get_resume(num=12)`：走飞牛 `/play/list`，用 `ts` / `duration` 换算播放进度，`watched=1` 自动过滤；
- `get_latest(num=20)`：走飞牛 `/item/list` 按 `create_time` 倒序；**服务端不支持全库查询时自动退化为逐库取再合并**；
- `get_episode_image_by_id()`：补齐转发契约。

## 缺陷 2：所有封面图 404

飞牛接口返回的图片是**相对路径**（如 `media/img/x.jpg`），客户端此前直接拼 `host + path`，
少了 `/api/v1/sys/img` 前缀（MoviePilot `__build_img_api_url` 的口径）⇒ 图片全部取不到。

**修复**：新增幂等助手 `__abs_image_url()` 统一处理（已是绝对 URL 则原样返回，兼容 list 形态），
`get_remote_image_by_id` / `get_local_image_by_id` 改用它，并给 `get_libraries` 补充库封面 `image` / `image_list`。

## 缺陷 3：类型判定只认英文（存量 bug，影响面更大）

`media_server.py` 判断条目类型时写死了 `['Movie', 'movie']` / `['Series', 'show']`，
而 **ugreen 与飞牛客户端输出的是 `MediaType` 中文值**（「电影」/「电视剧」）⇒

- 首页统计的**电影数 / 剧集数恒为 0**；
- **剧集根本不会去调 `get_tv_episodes()`** ⇒ 剧集的季集信息为空 ⇒ 剧集「是否已存在」的判断永远失败。

**这是 v6.2.0 之前就存在的问题**（绿联影视同样中招），不是飞牛引入的。
**修复**：改用 `MediaDb.MOVIE_TYPES` / `TV_TYPES`（本身同时含中英文），与库侧口径统一。

## 改动文件

| 文件 | 变更 |
|---|---|
| `app/mediaserver/client/trimemedia.py` | +3 个方法（`get_resume` / `get_latest` / `get_episode_image_by_id`）+ 新增 `__abs_image_url` + 图片与库封面接线 |
| `app/mediaserver/media_server.py` | 类型判定由英文白名单改为 `MediaDb.MOVIE_TYPES` / `TV_TYPES` |

合计 2 文件 +216 / −4。

## 测试

| 层级 | 脚本 | 项数 | 结果 |
|---|---|---|---|
| 抽象方法契约 | `_verify_trimemedia_contract.py` | 19 | ✓ |
| 声明 | `_verify_trimemedia.py` | 26 | ✓ |
| 运行时 | `_verify_trimemedia_runtime.py` | 20 | ✓ |
| 端到端（v6.2.0 既有） | `_verify_trimemedia_e2e.py` | 36 | ✓ |
| 存量回归 | `_verify_trimemedia_regression.py` | 31 | ✓ |
| **本次专项** | `_verify_trimemedia_fix.py` | **63** | ✓ 真实 HTTP 假服务端 + 服务端侧复算 authx，覆盖 `get_resume` / `get_latest` / 图片 URL / 库封面 |
| **类型判定** | `_verify_mediasync_type.py` | **21** | ✓ 加载真实 `media_server.py`，注入中 / 英文口径真跑同步 |

合计 **216 项断言，0 失败**。

其中最有分量的一条：**修复前中文口径下 `get_tv_episodes()` 完全不会被调用**；
修复后正确调用、统计 1 / 1，且 emby 的英文口径未受影响。

## 踩坑

- **「同步成功」≠「首页有内容」**：两者是不同链路（见「背景」），排查时别往同步方向使劲。
- **抽象方法契约会漏**：`_IMediaClient` 只列了 19 个方法，但 `MediaServer` 转发的方法**多于 19 个**
  （`get_resume` / `get_latest` / `get_episode_image_by_id` 都不在其中）⇒ 真正可靠的做法是
  **用脚本自动交叉核对「`MediaServer` 转发的方法」vs「客户端已实现的方法」**，而不是人肉数抽象方法。
- **`.git/packed-refs` 被文本模式写成 CRLF**：v6.2.0 推送后写跟踪引用时用了文本模式 `open(..., 'w')`，
  Windows 把 `\n` 全转成 `\r\n` ⇒ 之后**所有 git 命令**报 `fatal: unexpected line in .git/packed-refs`
  （push / fetch / ls-remote 全废），而 `git status` 竟也不报错（当时只用 REST 复核，REST 不读该文件）。
  ⇒ **`.git` 下的文件一律 `open(..., 'wb')` 二进制写**；已沉淀 `_fix_git_refs.py` 作为发版第 0 步预检。

## 兼容性

- 修复均为**补齐 / 放宽**，无破坏性变更；
- `media_server.py` 的类型判定改动**同时惠及绿联影视**（同一 bug 的另一受害者）；
- 存量 emby / jellyfin / plex 的英文口径**未受影响**（已实测）；
- 无数据库迁移、无接口变更、无新依赖。

---

# v6.2.0 — 媒体服务器接入「飞牛影视」

## 背景

飞牛 OS（fnOS）自带影视中心「飞牛影视」，内部代号 **TrimMedia**。MoviePilot 已支持接入，本项目参照其实现完整移植，使 nas-tools 也能把飞牛影视当作媒体服务器使用（媒体库展示 + 下载控重 + 入库后刷新媒体库）。

## 协议要点（三重认证）

| 层 | 说明 |
|---|---|
| API Key | 固定值 `16CCEB3D-AB42-077D-36A1-F355324E4237`，**参与签名**（非用户配置项） |
| authx 签名 | `MD5(盐_路径_nonce_ts_bodyHash_apikey)`，盐 = `NDzZTVxnRKP8Z0jXg1VAMonaG8akvh`；`api_path` 需以 `/v` 开头；GET 用**未 URL 编码**的 query 串算 bodyHash |
| Token | 登录响应下发，走 `Authorization` 头（**无 Bearer 前缀**） |
| 访问码 | 可选，`GET {设备根}/c/{code}`（**在设备根路径，不在 /v 下**），404 = 码错 |

登录优先 `POST /api/v2/user/loginByPassword`（密码传 **SHA256 hex 小写摘要**），v2 不存在才回退 v1 `POST /api/v1/login` 明文。

## 改动文件

| 文件 | 变更 |
|---|---|
| `app/mediaserver/client/trimemedia.py` | **新增** +1069（`_TrimeApi` 29 方法 + `TrimeMediaClient` 30 方法） |
| `app/conf/moduleconf.py` | +94（`MEDIASERVER_CONF` 新增 `trimemedia` 卡片，10 个字段） |
| `config/config.yaml` | +23（新增 `trimemedia:` 段） |
| `app/utils/types.py` | +1（`MediaServerType.TRIMEMEDIA = "飞牛影视"`） |
| `web/static/img/mediaserver/trimemedia.png` | **新增** 5234 bytes（512×512 图标） |

合计 5 文件 +1187 行 / **0 删除**。

## 前端零改动

`mediaserver.html` 与 `macro/form.html::gen_form_config_elements` 均为数据驱动（循环 `MEDIASERVER_CONF`），支持 `switch` / `select` / `text` / `password` / `textarea` 五种类型自动出控件 ⇒ 新增媒体服务器**不需要改任何前端文件**。

客户端通过 `SubmoduleHelper.import_submodules('app.mediaserver.client', filter_func=lambda _, obj: hasattr(obj, 'client_id'))` 自动发现，无需注册表；`__build_class` 用 `match(ctype)` 匹配类型。

## 测试

| 层级 | 脚本 | 项数 | 结果 |
|---|---|---|---|
| L1 契约 | `_verify_trimemedia_contract.py` | 19 | ✓ `_IMediaClient` 19 个抽象方法全覆盖 |
| L2 声明 | `_verify_trimemedia.py` | 26 | ✓ 语法 / 卡片 / 字段 / 四处一致 / authx 算法自检 |
| L3 运行时 | `_verify_trimemedia_runtime.py` | 20 | ✓ 真实导入 + 发现契约 + **authx 与 MoviePilot 参考实现逐字符一致** |
| L4 端到端 | `_verify_trimemedia_e2e.py` | 36 | ✓ 本地假 HTTP 服务端跑通全链路，**42 个 `/v` 请求全部通过服务端侧 authx 校验** |
| 存量回归 | `_verify_trimemedia_regression.py` | 31 | ✓ emby / jellyfin / plex / ugreen 零影响 |

合计 **132 断言 + 19 契约项，0 失败**。

## 踩坑

- **MoviePilot 默认分支是 `v3` 不是 `main`** ⇒ 直接按 main 拉源码会 404。
- **`raw.githubusercontent.com` DNS 被封**（`getaddrinfo failed`），两个企业代理同时失效 ⇒ 改用 **Contents API**（`/repos/{owner}/{repo}/contents/{path}?ref=v3` 返 base64）拉源码。
- **`MediaType.MUSIC` 枚举不存在**：音乐库类别误用后会 `AttributeError`，**AST / `py_compile` 完全抓不到**，运行才炸 ⇒ 已改 `MediaType.UNKNOWN`，并加静态断言防回归。
- **`get_play_url` 类型两形态**：`item_info` 可能收到飞牛原始 dict（`type="Movie"`）或 `_build_item` 产物（`type="电影"`） ⇒ 新增 `_to_trime_type()` 归一化，否则恒走 `/other/` 分支。
- **假服务端要在服务端侧独立复算 authx**（客户端自算自比无意义），且服务端需对 query **先 `unquote` 再复算** —— 客户端用 requests `params` 交中文会被编码成 `%E6...`，与「未编码」口径不一致（真实飞牛服务端即此行为）。
- **`-14 Task duplicate`**：飞牛扫描接口在已有任务在跑时会拒绝 ⇒ 客户端先调 `task_running()` 规避。
- **图标背景色冲突**：飞牛初版用 `bg-purple` 与 jellyfin 重复 ⇒ 改 `bg-cyan`（未占用）。

## 兼容性

- **纯增量**：5 文件 +1187 行，**0 删除**；
- 存量 4 个媒体服务器（emby / jellyfin / plex / ugreen）的名称、背景色、`test_command`、枚举值、`config.yaml` 段**全部未变**（已逐项快照比对）；
- 无数据库迁移、无接口变更。

---

# v6.1.1 — 设置页「识别与搜索」区排版归组

## 症状（无头浏览器实测）

| 行 | 改动前行高 | 内容 |
|---|---|---|
| 1 | 36px | 4 个开关 |
| 2 | **80px** | 3 个开关 + 1 个数值输入框 |
| 3 | **80px** | 1 个数值输入框 + 2 个开关（右下角空一格） |

## 根因

1. **开关与输入框混排**：开关控件高 18px、数值输入框高 36px，同处一个网格 ⇒ 行高 36/80 交替，参差不齐；
2. **两个数值框分散**：分别落在第 2 行第 4 列与第 3 行第 1 列，同类控件对不齐；
3. **输入框挤占列位**：第 3 行只剩 3 个元素，右下角留白。

## 改动文件

### `web/templates/setting/basic.html`

把这一区**拆成两个语义分组**，各占一整个 `col-12`：

- **开关组**：外层 `col-12` + 内层 `row`，9 个开关各自 `col-12 col-sm-6 col-xl-3`；
- **数值组**：外层 `col-12` + 内层 `row`，2 个数值项各自 `col-12 col-md-6 col-xl-6`。

> 列类由原来的 `col-12 col-xl-3` 改为带 `sm`/`md` 断点的版本，使小屏自动降列。

## 测试

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| `_verify_lab_layout.py` | **Jinja 真实渲染** + 11 个字段 id 完整性 + 列类统计 + div 配平 + 输入框数量不变 | 19 / 19 |
| `_measure_cdp.py` | 无头 Edge（CDP）实测 3 种视口下的行高与列宽 | 1400 / 1200 / 900px 均通过 |
| `_diff_lab_structure.py` | 改动前后 label / span / input / div 数量与 id 总数对比 | 全部一致（div 各 +4 且配平） |

另：`python -m compileall app web config.py version.py` 通过。

## 兼容性

- **纯排版改动**：不动任何 `id`、`name`、字段与保存逻辑；
- `label` / `span` / `input` 数量与改动前**完全一致**（已逐项比对）；
- 无数据库、配置、接口变更。

## 踩过的坑

- **无头 Edge 的 `--dump-dom` 不再等待脚本执行**（新版 headless）：用它取不到 JS 注入的量测结果，拿回的仍是原始 HTML。⇒ 改用 **CDP**（`--remote-debugging-port` + `Runtime.evaluate`），并补 `--remote-allow-origins=*` 否则 WebSocket 握手 403。
- **本机外网 CDN 不可达**：量测页改用项目自带的 `web/static/css/tabler.min.css`（与线上完全一致，且离线可跑）。
- **`open()` 默认会把 CRLF 归一为 LF**（universal newlines）⇒ 读 CRLF 模板做行切片时必须 `newline=''`，否则 `split('\r\n')` 只得到 1 行。
- **切片边界要数清 div 归属**：本区切到原第 1056 行（关闭最后一个 `col`）即可，结尾**不可**再补 `</div>`（关闭 `.row` 的那行本就在切片之外）——多补一个会导致 div 不配平，靠结构对比脚本发现。

---

# v6.0.9 — 媒体库同步「响应速度」优化 + 界面精简

## 症状

1. **点了没反应**：点首页「媒体库」打开同步弹窗后，界面要停留一小会儿才弹出来；
2. **进度条长时间不动**：点「开始同步」后，进度条会长时间停在「正在获取 XXX 数据...」；
3. **同步整体偏慢**：条目多时整个流程耗时明显。

## 根因（前端 2 处 + 后端 2 处）

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| 1 | `index.html::show_mediasync_modal` | 弹窗在**两次串行 ajax**（`refresh_process` → `mediasync_state`）都返回后才 `modal('show')` | 服务器稍慢就「点了没反应」 |
| 2 | `index.html::start_media_sync` | 用 `setTimeout(..., 1000)` 延迟 1 秒才建立进度流 | 点「开始同步」后 1 秒内界面无任何反馈 |
| 3 | `media_server.py` | `items = list(self.get_items(lib_id) or [])` —— `get_items` 是生成器且**每条都要单独请求详情接口**，`list()` 必须等全部抓完才返回 | 进度条在整个媒体库抓完前纹丝不动 |
| 4 | `media_db.py` | 每条目一次 `commit()` | SQLite 每次 commit 都是一次 fsync，NAS 上尤其慢 |

## 改动文件

### `web/templates/index.html`

- `show_mediasync_modal`：**先 `modal('show')`**（带「正在获取同步状态...」占位），再异步取状态；
- `start_media_sync`：去掉 1 秒 `setTimeout`，改为**立即建立进度流**并显示「正在启动同步...」；
- SSE `onmessage`：仅在 `ret.text` 非空时覆盖文案，避免瞬时空值抹掉占位提示；
- 移除「全选 / 全不选」两个按钮与「不勾选任何库时…」提示行（保留「已选 N / M」计数与未勾选时的二次确认）。

### `app/mediaserver/media_server.py`

- 去掉 `list()` 预物化，改为 `for item in (self.get_items(lib_id) or []):` **边取边处理**，收一条就更新进度；
- 引入 `BATCH_SIZE = 50` 的写入缓冲，每批落盘后刷新一次进度，每个库收尾与全局结束各做一次兜底 flush；
- 条目级 `except` 中的 `ExceptionUtils` / `log` 调用再包一层 try/except，避免「异常处理器自身报错」逃逸出去中断整库循环。

### `app/db/media_db.py`

- 新增 `insert_batch(server_type, rows)`：一批一个事务，返回**实际写入条数**（跳过 `None` 条目）；
- 批内**每条用 `session.begin_nested()`（savepoint）隔离**：单条失败只回滚自己，其余照常入库 —— 否则「一条坏数据拖垮整批」会比旧版逐条提交更糟；
- `insert()` 保留（向后兼容）；新增 `import log`。

## 测试

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| `_verify_speed_ui_609.py` | 源码断言 + 界面精简 + 即时响应 + AST 结构 | 27 / 27 |
| `_verify_insert_batch_609.py` | **真实 SQLite**：批量写入 / 去重 / 空值 / savepoint 隔离 / 缓存清理 / 类型收敛 | 14 / 14 |
| `_verify_mediasync_v608.py`（回归） | v6.0.8 五根因 + 端到端（桩已同步支持 `insert_batch`） | 51 / 51 |
| `_verify_mediasync_clients_v608.py`（回归） | emby 客户端失败路径 / 容器下钻 / 混合库 | 37 / 37 |
| `_verify_media_db_v608.py`（回归） | 类型收敛 / 缓存键 / 缓存隔离 | 28 / 28 |

另：`python -m compileall app web config.py version.py` 通过。

## 兼容性

- 数据库表结构、配置项均未变，无需迁移；
- `insert()` 保留，旧调用点不受影响；
- 批量提交不改变最终入库结果（去重语义、类型收敛、缓存清理均与逐条一致，已有真实 SQLite 用例覆盖）。

## 踩过的坑

- **批量事务会把「单条失败」放大成「整批丢失」**：旧版逐条 `commit` 时坏数据只影响自己，改成一批一提交后必须补 savepoint，否则退步。测试用「不可 JSON 序列化的 seasoninfo」成功复现。
- **异常处理器自己也可能抛异常**：`log.error(...)` / `ExceptionUtils.exception_traceback(...)` 若失败，会逃出内层 `except` 被外层捕获 → 触发 `rollback()` → 整批回滚。⇒ 异常处理路径必须自身兜底。
- **`core.autocrlf=true` 下 `git status` 会对 LF 工作树报 `LF will be replaced by CRLF`**：只影响下次 checkout 的工作树显示，**不影响仓库存储**（本仓远端存储为 LF，`README.md` 除外，为 CRLF）。

---

# v6.0.8 — 我的媒体库同步全面修复

## 症状

点「媒体库同步」后弹窗永久停在 **「正在获取 XXX 数据...」**，进度条不动、不报错、结束不了；
`media.db` 里一条记录都没有，页面显示 0 条。Emby / Jellyfin / Plex 均命中；
绿联（Ugreen）因 `get_items` 恰好返回 list 而幸免。

## 根因（一条主因 + 四条放大器）

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| 1 | `app/mediaserver/media_server.py` | `items = self.get_items(lib_id)` 之后直接 `len(items)`，而 emby/jellyfin/plex 的 `get_items` 是**生成器** | `TypeError: object of type 'generator' has no len()`，整轮同步在第一个库就崩 |
| 2 | 同上 | `sync_mediaserver` 没有兜底，异常直接冒泡出线程 | `progress.end()` 永不执行 ⇒ 前端 `refresh_process` 拿到的 value 永远 < 100 ⇒ **弹窗永久卡在「正在获取...」** |
| 3 | `app/mediaserver/client/emby.py`（jellyfin 同构） | `get_items` 里逐个条目调 `get_iteminfo`，该函数在「无响应 / 非 200」时**隐式返回 None** | 下一行 `item_info.get(...)` 抛 `AttributeError`，被外层 `except` 吞掉后**整个库的 for 循环直接中断**，后面的条目全丢 |
| 4 | 同上 | `get_libraries` 只认 `CollectionType` 为 `movies` / `tvshows` | **混合库（该字段为 null）**、`musicvideos`、`homevideos` 三类库被静默丢弃 ——「动画电影」「综艺」「纪录片」这些库在同步列表里根本不存在 |
| 5 | `app/db/media_db.py` | `query()` 只按 `SERVER` + `TMDBID`/`TITLE` 查，**不区分媒体类型** | 同名电影与剧集（如《三体》）互相命中，把另一类型误判为「已存在」 |

## 改动文件

### `app/mediaserver/media_server.py`

- `get_items` 结果先物化成 `list` 再计数与遍历（`list(self.get_items(lib_id) or [])`）；
- 同步主体拆出 `__sync_mediaserver_impl()`，由 `sync_mediaserver()` 用 **`try/except/finally`** 包裹，**任何**异常都保证 `progress.end()` 落地并写明「同步异常终止」；
- 条目级 `try/except`：单条入库失败只记日志跳过，不再中断整库；
- `check_item_exists` 把媒体类型下推给 `media_db.query(mtype=...)`，从 SQL 层收敛同名电影/剧集。

### `app/mediaserver/client/emby.py`（`jellyfin.py` 同步修）

- `get_iteminfo` 统一契约：无响应 / 非 200 / 抛异常 / `json()` 为 None **一律返回 `{}`**；
- `get_items` 增加**条目级** `try/except`，单条详情失败时降级使用列表接口自带的字段（`Name`/`Type`/`ProductionYear`），**条目不丢、循环不断**；
- 类型分派补全：`BoxSet`（合集）、`Season`（季）、`Folder`（目录）继续下钻，单独出现的 `Episode` 按条目登记；
- 条目补 `image` / `link` 字段，与绿联客户端口径一致；
- `get_libraries` 的 `CollectionType` 兜底：`None`/空 → 混合库放行（类型留空，逐条判定），新增 `musicvideos` / `homevideos`；`music`/`books`/`games`/`livetv`/`channels` 跳过并**打日志**。

### `app/mediaserver/client/plex.py`

- `get_items` 增加条目级 `try/except`，并补 `image` / `link`；
- `get_libraries` 跳过非影视库时补日志（原先静默 `continue`，用户完全无从排障）。

### `app/db/media_db.py`

- `query()` 新增可选 `mtype` 参数，按 `ITEM_TYPE` 收敛电影/剧集，别名表覆盖中文（`电影`/`电视剧`）与英文（`Movie`/`Series`/`show`/`movies`/`tvshows`）；无法归一的类型宽松放行，避免升级后老数据被判为「不存在」；
- 归一在 `@cached` **外层**完成（cachetools 会先对实参取 hash，传入不可哈希对象时会在进函数体之前就抛 `TypeError`）；
- `insert` / `empty` 成功后清理查询缓存，避免缓存里残留被删除的 ORM 实例（`DetachedInstanceError`）。

### `web/templates/index.html`

- 媒体库列表加 **「全选 / 全不选」** 与 **「已选 N / M 个库」** 实时计数；
- **一个库都没勾选**时点「开始同步」会先弹确认。

## 测试

| 脚本 | 覆盖 | 结果 |
|---|---|---|
| `_verify_mediasync_v608.py` | 源码断言 + 前端 JS 配平 + 端到端桩执行（7 场景） | 51 / 51 |
| `_verify_mediasync_clients_v608.py` | emby 客户端四类失败路径 / 容器下钻 / 混合库取舍 | 37 / 37 |
| `_verify_media_db_v608.py` | 真实 SQLite 验证类型收敛、缓存键可哈希、缓存隔离 | 28 / 28 |

另：`python -m compileall app web config.py version.py` 全量通过；全部改动文件保持原有行尾（纯 CRLF）。

## 兼容性

- `media_db.query` 的 `mtype` 为**可选**参数，不传时行为与旧版一致；
- `MediaType` 枚举、`SyncLibrary` 配置、`MEDIASYNC_ITEMS` 表结构**均未变**，无需迁移；
- 混合库的 `type` 为**空字符串**，`sync_library` 里按 `movie`/`tv` 归档时不会被误选，按库 ID 勾选则照常同步。

## 踩过的坑

- **`ast.unparse` 抽取方法体放进桩类执行时，私有方法名不会触发名字改写**：`self.__foo()` 经 `ast.unparse` 输出后仍是字面 `__foo` 调用（Python 只在编译期对类体内出现的方法名改写）。因此桩类必须用 `setattr(cls, "__foo", fn)` 按字面双下划线名绑定；反之在类体内写 `X = fn` 会被改写成 `_Cls__X`，两边都对不上。
- **`@cached` 的实参哈希发生在函数体之前**：想在函数体里判 `None` / 归一类型来规避不可哈希实参是徒劳的，必须先在外层把实参归一成字符串，再调用被缓存的内部方法。

---

# v6.0.7 — 目录清理

## 新增文件
| 文件 | 说明 |
|---|---|
| `app/helper/clean_helper.py` | `CleanHelper`：核心服务（扫描 / 删除 / 结果摘要），无 UI 依赖 |
| `tests/test_clean_helper.py` | 31 项单元测试（阈值边界、嵌套、空目录、dry-run、符号链接、异常容忍等） |

## 改动文件
| 文件 | 改动 |
|---|---|
| `web/backend/pro_user.py` | `SERVICE_CONF` 新增 `clean_dirs` 卡片（服务菜单入口） |
| `web/action.py` | 注册 `clean_dirs_scan`（预览）/ `clean_dirs_run`（执行）两个 action |
| `web/templates/service.html` | 新增 `modal-clean-dirs` 弹窗：填参数 → 预览清单 → 二次确认 → 执行 |
| `web/main.py` | `service()` 路由传入配置默认值（`CleanDefaultRoot` / `CleanDefaultThreshold`） |
| `web/apiv1.py` | 新增 REST 接口 `/system/clean_dirs/scan` 与 `/system/clean_dirs/run` |
| `config/config.yaml` | 新增 `clean_dirs` 段（`root_path` / `threshold_mb`） |
| `README.md` | 新增 §2.27 说明 |

## 行为口径
- **判据**：子文件夹总大小 **≤ 阈值** 即命中；大小等于阈值也删。
- **单位**：1MB = 1024×1024 字节；空文件夹计为 0。
- **范围**：仅根目录的一级子文件夹；根目录本身与根目录下散落文件不受影响。
- **删除方式**：`shutil.rmtree` 递归删除整个文件夹。
- **dry-run**：预览模式只返回清单与预计释放空间，不落盘。
- **容错**：权限不足 / 文件占用 / 符号链接等记录日志并跳过，不中断；符号链接默认不跟随。
- **参数校验**：阈值非法（空 / 非数字 / nan / inf / 负数）回落为 0，避免误删有内容目录。

## 测试
`python -m unittest tests.test_clean_helper` → **31 项通过**（2 项依赖系统 symlink 权限的用例在无权限环境下自动跳过，其逻辑另由 mock 版用例覆盖）。

# v6.0.6 — 刷流弹窗版式收紧

## 改动（仅 `web/templates/site/brushtask.html`）
| 项 | 改动 |
|---|---|
| 保存目录 | 从独占一整行（`col-lg-12`）上移到与「标签 / 任务时长」同行（`col-lg-4`，约占内容宽 32%） |
| 4 个开关间距 | `me-4`（24px）→ `me-2`（8px），省 48px，确保一排放得下 |
| 预览/断言脚本 | 新增 720px 窄档硬断言 —— 真实渲染比 800px 预览更紧，预览必须比真实更严才可信 |

## 为什么上次没拦住
v6.0.5 的预览只在 800px 弹窗宽度下断言「4 开关一排」，实际浏览器渲染宽度更紧，导致 `me-4` 间距下第 4 个开关溢出换行。这次把断言压到 720px 再验一次，实测 800px 与 720px 双档均一排。

# v6.0.5 — 刷流弹窗精简 + 转移到媒体库开关修复

## 弹窗精简（只动 `web/templates/site/brushtask.html`）
| 项 | 改动 |
|---|---|
| RSS 地址 | 输入框去掉，保存目录由 `col-lg` 改为 `col-lg-12` 占满整行 |
| 同时下载任务数 | 去掉（输入框 + 校验 + 编辑回填 + 提交参数） |
| 部分下载（拆包）规则 | 整块去掉（3 个输入框 + 约 60 行校验逻辑） |
| 包含 / 排除 | 从选种规则中间移到该区**末尾**（发布年份之后） |
| 4 个开关 | `row + col-lg-4`（一行只放 3 个）改为 `d-flex flex-wrap` 一排 |

RSS 字段改为隐藏域，**提交参数（`brushtask_rssurl`）、编辑回填（`rss_url_show`）、新建清空三处接线保持不变** —— 老任务里已填的自定义 RSS 不会被编辑保存时静默清空，语义仍是「留空则用站点配置的 RSS」。

### 行为口径：保存时一并清空
前端不再提交「同时下载任务数」与「部分下载规则」→ 后端取到 `None` → 落库即空。**后端字段与开放 API 一行未动**（`apiv1.py` 的 `brushtask_dlcount` / `frac_*` 仍在）。
- **未重新保存的老任务仍按库里旧值运行**（任务卡片会继续显示「同时下载: N」徽标，正好当提示）
- **重新保存一次后**：不再限制同时下载数、不再走拆包下载
- 想恢复这两项能力：把字段加回界面即可，后端逻辑没坏

顺带修掉一个旧隐患：`dlcount` / `frac_*` 在「新建任务」时**从来没被清空过**（编辑完 A 再点「新建」会残留 A 的拆包规则），字段删掉后这个问题自动消失。

## 修复：刷流「转移到媒体库」开关的两处缺口
刷流任务的「转移到媒体库」关掉后，本意是「只做种、不整理」。之前有两种情况会漏掉：

1. **不勾「识别重命名」时开关失效**：守卫只挂在「识别转移」分支上，「只转移不识别（`__link`）」的两个入口 `file_change_handler` / `transfer_sync` 都没有守卫 → 关了开关照样被 link 进媒体库。
2. **种子刚下载完成的一瞬间可能被整理**：刷流跳过清单有 60 秒缓存，而路径判定只查一次。「下载目录 == 目录同步源目录 == 刷流保存目录」时，文件刚落地、清单还没刷新，就会被当普通文件整理入库（最长 60 秒窗口）。

### 改动
- `app/sync.py`：`file_change_handler` / `transfer_sync` 的 `__link` 分支补刷流守卫（守卫排在 `__link` 调用**之前**，不勾「识别重命名」时开关同样生效）
- `app/downloader/downloader.py`：
  - 新增 `BRUSH_SKIP_RECHECK_INTERVAL = 5`
  - `get_brush_skip_map` 支持 `min_interval`，可自定义缓存时长
  - 抽出 `__match_brush_skip`；`is_brush_skip_path` **未命中**时用短间隔兜底重查一次

**命中仍走 60 秒缓存（零额外开销）**，只有「疑似要整理」的文件才可能触发重查，且有间隔限流，不会拖垮目录同步。

### 不变的前提
「同目录不误伤」这条没有变：排除范围是**逐种子内容路径**（`content_path` 相对 `save_path` 拼出来的），不是整个保存目录 —— 同目录下的普通下载照常整理，**目录本身也不被排除**。

---

# 附录 · 本分支增强点详述（原 README 章节，2026-10-07 迁入）

> 本节原为 `README.md` 的「本分支增强点」章节。因篇幅增至 1400 余行、影响主页面阅读，
> 于 2026-10-07 整体迁入本文件归档；内容按发布当时的原文保留（含每条的「现象 / 原因 / 现在」），
> 未作改写。README 中只保留一页能力速览。

### 1. 刷流（Brush）能力强化

| 能力 | 说明 |
|---|---|
| 部分下载（拆包） | 可灵活配置下载的种子大小与比例，最大化提升刷流效率。**部分站点可能禁止，请慎用** |
| 限免到期检测 | 超过限免时间自动把速度降为 1B/s，防止流量偷跑（默认关闭；已测试 mteam、hdmayi） |
| 界面信息补充 | 展示已保种大小；种子明细增加种子大小、限免过期时间 |
| 任务时长 | 可为任务设定运行时长（小时，支持小数 0.5/2/24），到点自动停止下载新种；任务卡片按秒展示剩余时间（留空 = 不限时） |
| 刷流限制解除 | 取消新手刷流限制（`pt.force_enable_brush` 可强制开启） |

### 2. 索引能力扩展

- **保留 BT 能力与内置 BT 站点**，可继续索引和下载 BT 磁链、种子文件；
- **支持 Jackett 与 Prowlarr 索引器**，可与内置索引器并存，通过插件启用；
- **完美支持 MTeam 新架构**：站点配置里填 `api-key`（馒头 → 控制台 → 实验室 → 存取令牌）即可；
  - 站点签到同样支持，可在控制台的「登录设备活动记录」中核对签到（登录）记录。

#### 2.1 搜索策略：候选名轮询 + IMDb 兜底（v5.0.0）

「站点上明明有几十个资源，程序却只搜到一两条」的根因是**选词维度太窄** —— 一个片名只搜一次，
且只在第一轮零结果时才换第二个名字。现在改为：

| 机制 | 说明 | 配置项 |
|---|---|---|
| **英文名/原名补搜** | 第一轮有效结果不足阈值时，自动用另一个名称补搜一轮，两轮结果合并去重（只增不减） | `search_en_min_result`，默认 `5`；设 `1` = 老行为 |
| **候选名轮询** | 依次尝试：首选名 → 次选名 → 原名 → TMDB 别名（别名取自已缓存的 TMDB 数据，不额外请求） | `search_candidate_max`，默认 `2`（与老版本请求量相同） |
| **IMDb ID 检索** | 片名怎么搜都搜不到时，用 TMDB 给的 `tt` 编号直接查（编号全球唯一，不受译名/繁简/错字影响），走 Torznab `imdbid=` 与 Prowlarr `query=tt…` | 自动，无配置 |

两项新配置都在「设定 → 基础设置 → **实验室**」内，改完存盘即时生效。

#### 2.2 搜索结果可观测：不再有黑箱（v5.0.0）

搜索进度里那一行计数现在会自己说清问题，**不用再去猜是站点坏了还是关键词不对**：

```
馒头 7 条数据中，过滤 0，不匹配 6（名称脏1/TMDB无条目1/ID不符2/季集年不符2），错误 0，有效 1
```

- **「不匹配」拆成 7 个子项**：名称脏 / 相似度低 / TMDB 无条目 / ID 不符 / 年份不符 / 季不符 / 集不符。
  看到 `名称脏` 就去改**自定义识别词**，看到 `ID不符·年份不符` 就去改**匹配口径** —— 直接定位，不用逐条猜。
- **「搜不到」区分 5 种真实原因**：真的没资源 / Cloudflare 拦截 / Cookie 失效需要登录 /
  HTTP 异常（403、429、5xx）/ 请求超时。前四种都**不该去换关键词**，以前却被一律显示成
  「未搜索到数据」，属于反向误导。

#### 2.3 站点体检：七层分开测（v5.0.0）

**入口：站点管理 → 站点测试 → 每行右侧「体检」按钮**（可自定义探针关键词，默认 `1080p`）。

| 层 | 检查内容 |
|---|---|
| `L0` / `L0b` | 配置完整性 / **索引器定义**（内置规则或插件索引器是否匹配该域名） |
| `L1` | 网络可达（DNS + TCP；站点配了代理时，直连失败只报「注意」不报「异常」） |
| `L2` / `L3` / `L4` | HTTP 语义 / 反爬（Cloudflare）/ 登录态（Cookie 是否有效） |
| `L5` | **探针搜索**（真跑一次搜索，与真实搜索同链路） |
| `L6` | **字段完整度矩阵**（标题 / 下载链接 / 大小 / 做种数等的命中率） |

- 结论**只指向最底层的失败项**：L1 连不上时不会再提示你去改选择器；
- **最有用的一招**：把那个搜不到片名填进探针关键词，一次看清它死在哪一层；
- **体检全绿却仍搜不到** → 结论会明确提醒你问题在「选词 / 判定层」，站点没问题，别再折腾站点。

#### 2.4 判定层年份口径：跨类型比较（v5.0.1）

v5.0.0 之后仍出现过「**全站零有效**」的极端情况，日志形如：

```
The Thirteenth Floor 1999 CEE BluRay 1080p x264 TrueHD 5.1-UBits 与 1999 年份不匹配
Local:【Indexer】馒头 16 条数据中，过滤 0，不匹配 16（年份不符16），错误 0，有效 0
```

种子名里写着 `1999`、目标年份也是 `1999`，却被判「年份不匹配」。原因不是口径太严，
而是**两侧年份的数据类型不同**：

| 侧 | 来源 | 值 | 类型 |
|---|---|---|---|
| 种子名 | 增强识别V2 → `guessit` | `1999` | **int** |
| 目标媒体 | `set_tmdb_info` → `release_date[0:4]` | `'1999'` | **str** |

`1999 == '1999'` 恒为 `False` —— 所有带年份的资源被一次性误杀，只有名字里恰好没写年份的
极少数种子能通过（更早那次「馒头 7 条中，不匹配 6，有效 1」就是它）。

现在三处年份比较统一先做类型归一（**仍然严格相等**，不放松口径：目标年份 2011 的资源照样被拒），
并且年份不匹配的日志会把**两侧的值都打出来**，同类问题下次一眼可见。
另外判定层内部的异常不再被静默吞掉，而是计入「错误 E」并写入日志。

#### 2.5 种子链接下载：302 重定向与磁力链（v5.0.3）

搜索完成后，nas-tools 还要把命中的种子从站点拉回本地、再交给下载器。这一步在
M-Team 上曾经必然失败，日志只有一句：

```
无法打开链接：https://api.m-team.cc/api/rss/dlv2?sign=...&tid=...&uid=...
```

原因是 M-Team 走专属通道 `MteamUtils.get_mteam_torrent_req()`，那里用了
`allow_redirects=True`，重定向在请求层就被跟完了 —— 而 `save_torrent_file()`
里专门识别 **302 → `magnet:`** 的那段循环因此成了死代码。`requests` 不支持
`magnet:` 协议，跟随时会抛 `InvalidSchema`，被 `get_res()` 的 `except` 吞成
`None`，于是对外只剩「无法打开链接」。

v5.0.3 把 M-Team 分支改回 `allow_redirects=False`，与普通站点分支走同一套重定向
处理，磁力链得以被正确识别；同时让 `get_res(raise_exception=True)` 抛出带原始
信息的异常实例，并在失败时记录 `【MTeam】获取种子链接失败：<类型>: <原因>`。

> 提醒：下载失败的通知需要在该消息客户端里勾选「下载失败」开关，
> 否则失败是静默的，只能翻日志。

#### 2.6 下载失败的原因不再丢失（v5.0.4）

v5.0.4 之前，下载失败在日志里是**完全不可见**的：`__download_fail()` 只发事件与消息、
不写日志，整个日志只剩一行插件的事件分发记录（`处理事件：download.fail`）。
v5.0.4 起会明确打印：

```
【Downloader】馒头 逃出绝命街 添加下载任务失败：请检查下载任务是否已存在
```

同时记住一个容易误判的点：`download.add` 事件代表「**开始尝试**下载」，
**不代表下载器已经收下任务** —— 取种子失败时它也会先发出来。

#### 2.7 消息中心与下载目录的两处崩溃（v5.0.5）

v5.0.5 修掉两个会让「点下载没反应」的崩溃：

- **消息中心**：`@singleton` 装饰器会把类名替换成包装函数，`MessageCenter._seq`
  因此必然抛 `'function' object has no attribute '_seq'`。**所有系统消息写入全部失败**，
  下载失败通知既不进消息中心、也不推消息客户端。现已改用模块级计数器。
- **下载目录为空**：下载器配置里没配「下载目录」时该字段是 `None`，遍历时抛
  `'NoneType' object is not iterable`，表现为「下载目录」下拉框 500。现已统一归一化为 `[]`。

另外，下载器失配时的原因不再笼统：

```
【Downloader】馒头 异次元骇客 添加下载任务失败：下载设置「预设」未指定下载器，请到「设置 → 下载器」…
【Downloader】馒头 异次元骇客 添加下载任务失败：下载器 ID=99 不存在、未启用或初始化失败…
```

> 排查口诀：`@singleton` 装饰过的类，**类体里不要再写 `类名.属性`** ——
> 那时类名已经是个函数了。

#### 2.8 事件分发日志：不再打印函数对象的内存地址（v5.0.6）

v5.0.6 之前，日志里的事件分发记录是这样的：

```
处理事件：subtitle.download - [<function ChineseSubFinder.download at 0x7ff12ef285e0>, <function OpenSubtitles.download at 0x7ff12efe32e0>, <function Webhook.send at 0x7ff12ef97250>]
```

带内存地址、看不出插件全名，一行里挤三四个还换行。现在：

```
处理事件：subtitle.download - [ChineseSubFinder.download, OpenSubtitles.download, Webhook.send]
处理事件：transfer.finished - [LibraryRefresh.refresh, Webhook.send]
```

同一个提交里还修了三处：

- **「事件发了但插件没动静」**：原先用 `handler.__qualname__.split(".")[1]` 定位插件，
  遇到没有点号的函数（模块级函数、`functools.partial`）会抛 `IndexError` 并被吞掉，
  插件静默不执行。现在会明确告警是哪条监听函数没被识别；
- **插件报错不进日志**：插件内部异常原先走 `print()`，只到 stdout，
  `config/logs` 下的日志文件里完全看不到。现在改为 `log.error`，并带上插件名与方法名；
- 每条事件处理的成功路径补了 debug 级记录，方便判断卡在哪个插件。

#### 2.9 择优过程可解释 + 下载器配置脱敏（v5.0.7）

**一、日志里能直接看到「凭什么选它」**

远程搜索/订阅择优下载后，日志会打出候选清单与决胜键：

```
【Downloader】择优下载：按「站点优先」排序，候选 2 条；排序键 片名 > 规则优先级 > 站点优先级 > 做种数 > 季集完整度（均为数值越大越优先）
【Downloader】  第 1 名 学校 | 5.92G | 片名=逃出绝命街 规则优先级=100 站点优先级=100 做种数=96 季集完整度=0季0集 ← 选中
【Downloader】  第 2 名 HDTime | 4.31G | 片名=逃出绝命街 规则优先级=100 站点优先级=90 做种数=12 季集完整度=0季0集
【Downloader】择优下载选择：学校 | The.End.of.Oak.Street...BYNDR —— 决胜键：第 3 位「站点优先级」（100 > 90）
```

`决胜键` 是第 1 名与第 2 名**第一个不同的排序键**，也就是「为什么不是另一条」的直接答案。

**二、下载器密码不再进日志**

`【Downloader】下载器 before ... down_conf:` 原先会把 qBittorrent / Transmission 的
**明文密码**一起打出来（`'password': 'xxx'`）。现在 `password / token / cookie / api_key /
passkey / secret ...` 一律显示为 `***`，`host / port / username` 仍然保留 ——
排查连接问题够用，账号密码不再落进日志文件。

**三、`等待超时` 的意思被写清楚了**

站点搜索里的「等待超时（约 15 秒）」是**本地等待上限**，不是站点故障：后台请求可能仍在跑，
只是本轮结果作废。换下一个候选名会对同站点重新发请求 ——
所以「第一轮超时、第二轮成功」是正常的，不代表日志自相矛盾。

**四、下载目录没配会明确告警**

下载器没有可用下载目录时，日志会说明「本次交给下载器默认保存路径」，
不再只留一个 `down_dir: None` 让人猜。

#### 2.10 在线复核先筛年份：治「ID 不符」刷屏（v5.0.8）

搜索结果的归因里，「**ID 不符**」长期是数量最大的一类：

```
学校 55 条数据全部未通过（TMDB无条目2/ID不符24/季集年不符29）
```

它发生在**在线复核**分支 —— 只有种子名解析不出年份时才会走到这里。
原先它去 TMDB 查候选时**没带年份**，TMDB 就按名称返回一堆同名条目
（重启版、同一译名下的另一部片、不同年份的翻拍），这些条目名字相似度
照样能过阈值，一路走到最后的 `tmdb_id` 比对，然后集体被判「不符」。

现在两处在线复核分支查询时带上年份，并在候选循环里**再复核一遍**
（TMDB 的年份查询并非严格过滤，无该年条目时会放宽返回）：

```python
cached_tmdb_infos = self.media.get_tmdb_infos(title=en_title,
                                              year=match_media.year,
                                              mtype=match_media.type,
                                              page=1)
```

边界处理：**取不到年份的候选放行**（宁可交给后面的 id 比对，也不因
TMDB 缺字段误杀）。电影看 `release_date`、剧集看 `first_air_date`，
统一经 `_norm_year()` 归一化，与 v5.0.1 的年份口径一致。

模拟 5 条同名候选（仅 1 条同 id）：改动前 4 条会被记「ID 不符」，
改动后降到 1 条，目标本体仍在候选内。

> 顺带澄清一个常见误判：`is_torrent_match_sey` 的年份分支看着像「严格相等会误杀」，
> 实际它在 `merge_media_info` 之后执行，此时种子侧 `year` 已被卡片的
> `release_date[0:4]` 覆盖，两侧同源 —— 该分支既不会误杀也拦不住东西。
> 真正干活的年份闸门是 merge 之前执行的 `year_match`。

#### 2.11 单页片段的脚本只能注入一次（v5.1.0）

「站点」页的**连通性测试**按钮点了毫无反应，连按钮文字都不变 —— 而且
**F5 刷新后首次进入是好的，切走再切回来就坏**。

原因是它的 Web 结构：`navigation.html` 框架 + 页面片段经
`functions.js:70` 的 `page_content.html(data)` 注入 `#page_content`。
jQuery 的 `.html()` 会执行片段里的 `<script>`，所以**每导航一次片段脚本就重跑一次**。
片段里的顶层 `let`/`const`（如 `site.html` 的 `let SITEHEALTH_SITEID`）在全局
只能声明一次，二次注入直接抛 `Identifier ... has already been declared`；
该错误发生在**全局声明实例化阶段、先于任何语句**，于是整段脚本一句都不执行，
按钮绑定全部丢失 —— 而 `function show_sitetest_modal()` 早已提升为全局，
**弹窗照开、按钮全死**。

同一根因还波及另外两页（`service.html` 的备份/恢复按钮、
`statistics.html` 的排行缓存）。三处顶层 `let`/`const` 已统一改为 `var`。

> **给二次开发者的规则**：`web/templates/**/*.html` 里作为 SPA 片段注入的
> 脚本块，**顶层不要用 `let`/`const`**，一律用 `var`；需要局部变量就包进
> IIFE 或函数里。仓库内已有扫描脚本可自查。

#### 2.12 体检为什么有时跳过 L5，以及「经代理复测」（v5.1.2）

体检报告里 L5/L6 会显示「因 L2 请求未取到响应（reset）而跳过」。这不是漏测：
L2 都没拿到响应时，L5 走同一条网络路径必然同样失败，而抓取侧的等待上限是
**本地 15.5 秒**（`spider_search` 默认 `timeout=30`，循环 `sleep(0.5)`，31 次即到顶），
跳过它可以让这种必败的体检从约 16 秒降到 1 秒内。若 L2 拿到了响应（例如 HTTP 500），
L5 仍会照常执行。

另外，站点没打开「代理」开关时，**即使「基础设置 → 系统」里配了全局代理也是裸连**
（代码是 `if site.proxy: Config().get_proxies()`）。所以直连失败时，体检会自动用那个
代理再请求一次，并把结论写进 L2 明细：

| 明细 | 含义与下一步 |
|---|---|
| `经代理复测：可达（HTTP 200）` | 就是缺代理 —— 到站点管理打开该站点的「代理」开关 |
| `经代理复测：仍失败（reset）` | 代理本身不通，或该代理对本站点不可用 |
| （没有这一项） | 系统里没配代理，或该站点本来就已打开代理开关 |

> 注意 `config.yaml` 的默认值是 `proxies: {http: , https: }` —— 「键存在、值为空」，
> 判空必须逐项判，不能直接写 `if not proxies`。

#### 2.13 标签完全由你自己定义（v5.1.3）

**程序不再往下载器里写任何你没有填过的标签。** 历史版本会在你未开启「转移」时
强行给种子加上「已整理」，而这个名字在界面上无处可改 —— v5.1.3 起彻底去掉。

标签有四个来源，全部由你填写，程序只读取与拼接：

| 位置 | 作用域 | 说明 |
|---|---|---|
| 设置 → 标签 → 我的标签库 | 全局 | 只是候选清单，供各处下拉补全，便于统一命名 |
| 站点设置 → 站点标签 | 单个站点 | 该站点所有下载都带上 |
| 刷流任务 → 标签 | 单个任务 | 仅该刷流任务 |
| 下载设置 → 标签 | 单条下载设置 | 按下载设置生效 |

**分隔符统一为英文逗号 `,`。** 历史版本里下载设置与站点标签按分号拆分、
刷流任务按逗号拆分，同一个标签换个入口就匹配不上；现在四处一致。

「是不是已经整理过」这个判断不再依赖标签 —— v5.2.0 起改由程序自己的
**转移账本**承担，见下一节。

界面上的标签现在常驻显示：刷流任务列表的卡片标题旁直接是彩色徽标，
不用展开、不用悬停。

给二次开发者的提醒：改 `config.yaml` 后写盘**必须就地修改配置对象**，
不要 `dict(cfg)` 再存 —— config.yaml 是 ruamel 的注释感知结构，
浅拷贝会让全部说明注释静默消失（实测漂移 1343 字节）。

#### 2.14 整理去重改用「转移账本」（v5.2.0）

v5.1.3 把「已整理」标签去掉之后，还留着一个问题：**程序怎么知道某个种子
已经整理过了？** 答案一度是「自己把『已整理』填进标签里」，但这要求你自己维护，
忘记填就会重复整理。

v5.2.0 换成程序自己记账：

```yaml
pt:
  ledger_enable: true       # 记录「已整理过」的种子，下次不再重复处理
  ledger_max_rows: 5000     # 行数上限，超出自动删最老的（0 = 不限制）
  ledger_expire_days: 90    # 超过天数的记录定期清理（0 = 不清理）
```

**这份记录只存在程序自己的数据库里（表 `TRANSFER_LEDGER`），不会写入下载器标签**，
所以你的 qb 标签列表始终干净。

两个问题一次解决：

- **不会无限增长**：插入前先看总行数，达到上限就先删到 80% 再插，行数硬性封顶；
  另有按天数的过期清理（每小时最多执行一次，复用现有调度，不额外开线程）。
- **不会被误清空**：账本是独立表，不挂在 `/trh`（那是清 `TRANSFER_HISTORY`）
  任何用户可触发的清空入口上。

按每天 20 个种子估算，5000 条约覆盖 8 个月；即使一个都不清理，
一年的数据量也只有十几 MB（SQLite 上限是 281 TB）。

关掉 `ledger_enable` 也不会真的重复转移 —— 还有「目标文件已存在」这层兜底 ——
只是每轮都会白扫一遍下载目录并多打日志。

顺带修掉一个存在已久的判定 bug：开启「只处理 NASTOOL 标签」时，原实现用
字符串子串匹配，会导致 `NASTOOLX` 被误判成命中了 `NASTOOL`；现在改为按
逗号切分后精确比对。

#### 2.15 刷流只做种、年份过滤与默认复制（v5.2.2）

**「转移到媒体库」开关以前是死的。** 关掉它的本意是「只做种、不整理」，
但 v5.1.3 去掉「已整理」标签、v5.2.0 改用转移账本之后，这个开关再没有任何
代码读它 —— 关掉也照样刮削入库。v5.2.2 让两条自动入库路径都认它：
下载器监控按**种子 hash** 跳过（带 60 秒清单缓存，查库异常则降级为不跳过），
目录同步按**文件路径**跳过（按分隔符对齐前缀，`/brush/MovieB` 不会误伤
`/brush/MovieBB`）。只有明确关闭的任务才跳过，其余一律照常入库。

**三处「转移方式」下拉的默认值统一了。** 以前目录同步默认复制、下载器新增写死
硬链接、手动识别弹窗读 `media.default_rmt_mode`，三处口径不一。现在统一同源于
`media.default_rmt_mode`（出厂 `copy`），`filetransfer.py` 的兜底也从影子配置
`pt.rmt_mode` 换到同一个键；配置缺失或填了非法值一律回落复制，已保存的配置不动。
另需知道：`SystemUtils.link` 是裸 `os.link`，**跨卷会直接失败、没有 copy 兜底**，
同机跨盘转移时复制比硬链接稳。

**选种规则新增「发布年份」。** 四种取值：不限制 `#`、不早于 `gt#2000`、
不晚于 `lt#2020`、介于 `bw#2000,2020`（均含端点）。年份用 guessit 从标题解析，
《2012》《Blade Runner 2049》这类标题里的数字不会被误当成年份；
**解析不到年份不拦截**。保存任务时写入 `rss_rule["year"]`，每轮轮询新种时判定。

同区的「当前站点下载任务数」已移除 —— 它只有任务配了标签才生效，实际经常形同虚设，
且与「当前站点下载数」重叠；老任务里的该键不再读取，无需迁移。

#### 2.16 发布年份改用「大于 / 小于」，边界改为严格（v5.2.3）

「发布年份」的下拉文案由「不早于 / 不晚于」改成「大于 / 小于」，判断同步
从**含端点**改成**严格比较**：`gt#2000` 现在是**年份 > 2000**（2001 年起，
不再放行 2000 年），`lt#2020` 是**年份 < 2020**（至 2019 年，不再放行 2020 年）；
「介于」仍含两端。

**已保存的任务会立刻按新口径生效** —— 如果原先的「不早于 2000」本意是想连
2000 年一起放行，把值减 1（填 1999）即可。任务卡片上的徽章、开放 API 说明
与运行日志里的措辞一并同步。

#### 2.17 实时日志会自动滚到最新了（v5.2.4）

「实时日志」弹窗以前只会把新日志追加进去，不保证跟着滚。原因不是滚动容器坏了，
而是判定「要不要自动跟随」用的是 `scrollTop + offsetHeight >= scrollHeight`
这种**零容差**写法，并且只在追加前算一次 —— 滚动位置差 1px 到不了理论最大值
（物理像素吸附、亚像素布局都会），判据即为假，而**为假之后就不再跟随**，
也不会自己恢复。

现在改成：默认永远跟随最新；用户手动往上翻才暂停，此时按钮栏会出现
**「回到底部」**，点一下或自己滚回底部即恢复跟随。打开弹窗、切换日志来源
都会重新从最新开始跟随。

#### 2.18 下载失败自动换下一个候选；刷流日志中文化（v5.2.5）

**择优下载不再「一击定生死」。** 以前每个片名只保留排序最高的那一条，第 1 名
添加下载失败（例如站点下载域名被网络阻断）就整部片子判死，排在后面、完全可用的
候选一次都不会被尝试 —— 日志里会出现「候选 48 条」却「未下载到资源」这种自相矛盾
的结果。现在同名候选会按同一套择优顺序挂成**回退备选**，第 1 名失败自动试下一个，
直到成功。新配置项 `laboratory.search_retry_max` 控制上限（默认 20；填 `0` 关闭回退、
恢复到老行为，填 `-1` 试完全部同名候选）；**中间候选失败只记一条日志**，只有全部
候选都失败才发一次通知，不会产生几十条飞书消息。

**刷流日志全中文化。** 日志正文里的 `【Brush】` / `【BRUSH】` 前缀共 57 处改为
`【刷流】`，`peer_count:` / `threshold:` / `left:` / `right:` / `pubdate:` / `year:` /
`title:` 等字段名一并改成中文，`seed_size not configuration` 也换成了完整中文说明；
`H&R`、`FREE`、`2XFREE`、`RSS`、`Cookie` 等 PT 专业词按原样保留。

#### 2.19 按种子自动判断媒体类型，自动归类到下载器分类（v6.0.0）

**这是本项目第一次具备「不依赖 TMDB 的媒体类型判断」能力。** 以前判断类型只有一条路 ——
查 TMDB（`__get_tmdb_type`），要求 API Key、要求联网，而且 TMDB 本身只认电影和电视剧，
遇到软件、音乐、游戏、电子书、教程这类资源给不出结论；它返回的「未知」也不等于「其它」。
更实际的问题是，这套体系只管 nas-tools 自己下载过的任务 —— 下载器里手工添加的、刷流下载的、
以前就在的任务，一个都分不到类。

现在新增了一套**纯本地、完全离线**的判定引擎（`app/utils/media_classifier.py`），
按可靠性串行降级：**站点分类 / 下载器分类字段 → 非影视关键词黑名单 → 剧集特征
（`SxxExx` / 第x季 / 第x集 / 全xx集 / 完结 / 综艺）→ 电影特征（年份）→ 文件清单二次确认**
（最后一步可选，默认关闭）。结论只有三类：**电影 / 电视剧 / 其它**，动漫并入电视剧不单列。
因为不联网、不调 API，可以对下载器里的**全量任务**周期性扫描，判定开销可忽略。

配置入口就在「下载器设置 → 编辑下载器 → 下载目录设置」的**「分类标签」列**（v6.0.2 起
把原先独立的「分类标签」+「自动分类」两列合并成了一个下拉 + 一个输入框）：

- 选 **自动判定**：参与自动分类扫描 —— 判定结果与本行「类型」一致的任务归入该分类
  （本行类型为「全部」则任意类型都归入）；下载新任务时也按媒体类型推导分类名。分类名
  恒为判定结果本身（电影 / 电视剧 / 其它），动漫并入电视剧。
- 选 **自定义**：填写固定分类名（不能填「自动」，它是「自动判定」的保留值），
  **不参与自动分类扫描**，只在下载新任务时归入该分类。
- **输入留空**：这一行不启用 —— 既不参与扫描，下载时也不写分类名。

「未启用」的判据只有一条：取值去掉首尾空白后为空（界面回显、提交保存、后端读取三处一致）。
规则按界面从上到下**先命中先用**，所以可以把「电影」行放前面做精细化；选「自动判定」的行会
整体高亮。一行都不选「自动判定」就等于关闭功能，**不需要额外的总开关**。

为保护数据做了三道防线：**建分类绝不带保存路径**（避免 qB 连带搬移任务文件）、
**默认跳过开了「自动种子管理」的任务**（qB 改动这类任务的分类会连文件一起搬走，可能搬走正在
做种的数据）、**挡空 ids**（避免 qB 把空 hashes 解释成「全部任务」而刷掉整个分类）。
本功能仅对 qBittorrent 生效，Transmission 没有「分类」概念、只有 labels；另外分类名建议与
qB 里现有分类对齐 —— 删种策略的「分类过滤」与下载设置的「分类隔离」都依赖这个字段。
#### 2.20 下载器设置界面重做：分区、卡片与统一的问号说明（v6.0.1）

「编辑下载器」弹窗做了一次纯界面改版，**没有改任何后端行为**，表单提交的字段一个字没变，
所以升级后老配置照旧可用。改动集中在「看得清、找得到」：

- **表单分区**：基本信息 / 连接设置 / 下载行为 / 下载目录设置 四块各带标题；
- **下载目录行卡片化**：每行先给一排列小标题（类型 / 二级分类 / 分类标签 /
  下载保存目录或 ID / NAStool 访问目录）再给控件，不再靠占位文字猜；选了「自动判定」的
  行整体高亮；折叠标题右侧显示**规则条数徽标**；
- **说明统一成 `?` 气泡**：所有字段说明由「鼠标停在输入框上」的原生气泡改为统一的
  `?` 图标 + tooltip，支持多行、宽度放宽，长说明不再被压成一条细线；
- **红底提示条改为小角标**：「种子管理模式」与「自动分类」的相互影响不再常驻占版面，
  收进标题行的角标里 —— 蓝底 `i` 为一般说明、琥珀 `!` 为需要注意，悬停或点击展开。

需要注意的一点：下载目录行是运行期动态插入的，页面装载时的 tooltip 初始化扫不到新节点，
因此这段逻辑额外做了插入后初始化、删除前销毁。

#### 2.21 「分类标签」与「自动分类」两列合并成一个下拉（v6.0.2）

下载目录设置原本有两个都跟「分类」有关的列：「分类标签」填 qB 分类名、「自动分类」选
「自动 / 电影 / 电视剧 / 其它」。两列看起来像重复配置，职责却不同 —— 配一行要在两列之间
来回看，很容易只填一列就以为配好了。

现在合并成**一列「分类标签」**，控件是一个下拉 + 一个输入框：

| 下拉 | 输入框 | 含义 |
|---|---|---|
| 自动判定 | （隐藏） | 参与自动分类扫描，分类名由系统按媒体类型推导 |
| 自定义 | 填写 qB 分类名 | 只写分类名，**不参与**自动分类扫描 |
| 自定义 | 留空 | 这一行**不启用** —— 既不参与扫描，下载时也不写分类名 |

「未启用」的判据只有一条：取值去掉首尾空白后为空（界面回显、提交保存、后端读取三处同规则）。

**老配置不用迁移**：存储复用原 `auto_category` 字段（`空` = 不启用、`自动` = 自动判定、
其它值 = 自定义），原 `label` 字段置空不再写。`auto_category` 为空但 `label` 有值的旧配置
一律当作**自定义**，行为与升级前一致；旧「固定档」值（电影 / 电视剧 / 其它）同样落成自定义、
不参与扫描。老数据只在**读取时归一、不回写库**，打开看一眼不保存不会改动配置。

⚠️ 「自动」是「自动判定」的保留值，选「自定义」时分类名**不能填「自动」** ——
提交时会直接拦下并提示（不拦的话会被读成自动判定，等于悄悄开了扫描）。

#### 2.22 刷流任务可设「任务时长」，并收敛速度限制入口（v6.0.3）

刷流任务以前只能手动启停。现在任务级新增「**任务时长**」（小时、支持小数）：

- `0.5` = 30 分钟、`2` = 2 小时、`24` = 一天，**留空 = 不限时**（与以前一样）；
- 取值范围 **0.1 ~ 720 小时**，前后端共用同一归一函数、开放 API 同规则校验，非法值直接拦下并提示；
- 到点自动把任务置为「**停用**」并清空计时起点；**已在跑的种子不动**，收尾交给既有删种规则；
- 任务卡片新增「**剩余时间**」一栏，按秒递减（本地倒计时，不轮询后端）。

计时起点独立于创建时间 —— 「停用」后再启动会**重新计时**，每次启动都是完整时长。
检查粒度 60 秒（独立短周期任务，**没有**复用 300 秒的删种扫描）。

**两处速度限制入口一并移除**，统一由下载器自身设置：

| 位置 | 字段 | 实际情况 |
|---|---|---|
| 任务配置 | 上限上传速度 / 上限下载速度 | 已失效的死开关（判定分支只写日志、不拦截） |
| 选种规则区 | 上传限速 / 下载限速（KB/S） | 真正写进下载器的限速 |

前端表单、校验、后端取值、开放 API、数据模型列全部同步清理；老任务里遗留的
`upspeed` / `downspeed` 读出来没人引用，**不需要数据迁移**。⚠️ 删种规则里的
「平均上传速度」（`avg_upspeed`）是另一套逻辑，**未受影响**。

另修一处**既有问题**：刷流任务弹窗关闭再打开时，上次校验失败留下的红框不会消失 ——
新建 / 编辑两个入口现在都在弹出前先清掉弹窗内的 `.is-invalid`（只动样式类，不动输入值）。

#### 2.23 「下载目录设置」字体统一，折叠箭头换实心三角（v6.0.3）

「下载器设置」弹窗里，「下载目录设置」这一块原先自成一套字号（标题 15px 深色、卡片标签 12px 灰），
与同页面「监控 / 转移方式 / 标签隔离 / 目录隔离」的 `.form-label` 不是一套。

- 字号 / 字重 / 颜色 / 行高 / 字间距在 `#modal-downloader` 上**集中定义一次**，其余规则只引用令牌；
- 折叠标题改为与分区标题同源（13px 灰），卡片标签直接挂 Tabler 的 `.form-label`；
  紧凑控件（`-sm`）只保留更小的内边距，文本字号与 `.form-select` 对齐；
- 折叠箭头由 `▸`（U+25B8，Unicode 里的**小**三角）换成 `▶`（U+25B6 实心三角），
  字号改为跟随折叠标题 —— 符号与标题永远一样大，标题字号以后再调箭头自动跟随。

#### 2.24 修复：删掉媒体库里的目录后又被拷回来（v6.0.4）

「媒体整理 → 历史」里删掉媒体库文件（只删目的端）之后，下一轮目录同步会把它当成
从没整理过的新文件，重新识别、重建目录，**把刚删掉的整份拷回媒体库**。

原因是「删文件」顺手清掉了去重标记。识别重命名模式下靠 `TRANSFER_BLACKLIST` 判断
「整理过没有」，而删除入口在判断删除范围**之前**就无条件清了标记 —— 三个口径都会执行。

- **「只删媒体库文件」现在保留标记**（源文件还在，它确实整理过了）；
  「只删源文件 / 两者都删」才清（源文件都没了，标记没有意义）。
- **「清空记录」只清看板上的转移历史**，不再重置去重状态。
- **「目的文件已存在且大小一致」时补记标记** —— 这类文件以前永远入不了账，
  每轮扫描都重新识别 + 重复告警，不会自愈。大小不一致时不记，保留洗版机会。

想强制让整个媒体库重新整理一遍，用「**清理转移缓存**」（`/tbl`）—— 它仍然整表清空，
是唯一的重置入口。**升级不需要数据迁移**，现有标记原样生效，不会触发全量重拷。

⚠️ 语义变化：「删除媒体库文件」不再是「单条重做」的入口；单条重做请用「**重新识别**」
（走手动整理，不走目录同步，本来就不受去重标记约束）。

#### 2.25 刷流任务弹窗精简，「转移到媒体库」开关补齐（v6.0.5）

刷流任务的新建/编辑弹窗只留真正要配的项：**去掉 RSS 地址输入框**（保存目录占满整行）、
**去掉「同时下载任务数」与「部分下载（拆包）规则」**、「包含 / 排除」移到选种规则末尾、
4 个开关排成一排（窄屏自动换行）。

- RSS 改为隐藏域，**提交、回填、新建清空三处接线不变** —— 老任务里已填的自定义 RSS
  不会被静默清空，语义仍是「留空则用站点配置的 RSS」。
- 「同时下载任务数」与「部分下载规则」**前端不再提交**，保存后落库为空：
  **未重新保存的老任务仍按旧值运行**（卡片会继续显示「同时下载: N」徽标），
  重新保存一次后即不再限制同时下载数、不再走拆包下载。
- 后端字段与开放 API **一行未动**，想恢复把字段加回界面即可。

同时修掉刷流「**转移到媒体库**」开关的两处缺口：

1. **不勾「识别重命名」时开关失效** —— 「只转移不识别（`__link`）」的两个入口
   （`file_change_handler` / `transfer_sync`）原本没有刷流守卫，关了开关照样会被
   link 进媒体库；现在都补上了。
2. **种子刚完成时可能被误整理** —— 刷流跳过清单有 60 秒缓存，而路径判定只查一次；
   现在**未命中**时会用 5 秒短间隔兜底重查一次（命中仍走 60 秒缓存，零额外开销，
   且有间隔限流，不会拖垮目录同步）。

「同目录不误伤」的前提没有变：排除范围是**逐种子内容路径**，同目录下的普通下载
照常整理，目录本身也不被排除。

#### 2.26 刷流弹窗版式收紧（v6.0.6）

v6.0.6 把「保存目录」上移到与「标签 / 任务时长」同行（`col-lg-4`，约占内容宽 32%），
不再独占整行；4 个开关间距从 `me-4` 压缩到 `me-2`（省 48px），确保一排放得下。

- 仅动 `web/templates/site/brushtask.html`，RSS 隐藏域、各字段接线、4 个开关 id 契约均未变。
- 配套预览脚本新增 **720px 窄档硬断言**，保证真实浏览器（渲染比 800px 预览更紧）下也不折行。

#### 2.27 目录清理：批量删除过小的子文件夹（服务 → 目录清理）

下载目录里常常残留一堆「下载失败 / 只有几 KB 样本」的小文件夹。**服务 → 目录清理**
可以一次把它们找出来删掉：

- 填「**根目录**」与「**大小阈值 (MB)**」，点「**预览**」先看清单（含每个目录的大小与
  预计释放空间），确认无误再点「**执行删除**」——**先看后删，不会盲删**。
- **判据**：子文件夹总大小 **≤ 阈值** 即命中；大小按递归累加内部所有文件计算，
  **1MB = 1024×1024 字节**，空文件夹记为 0。
- **只动一级子文件夹**：根目录本身、以及根目录下散落的文件都不受影响；深层子目录
  不单独列为待删项（它属于其父目录的体积）。
- **安全护栏**：符号链接（目录与文件）默认跳过，既不计入大小也不删除；权限不足、
  文件被占用等异常**记日志后跳过，不中断整体流程**，失败项会在结果区单独列出。
- 根目录与阈值都可在 `config/config.yaml` 的 `clean_dirs` 段预置（留空则每次在界面上填）；
  阈值填非法值时会回落为 0（只清空文件夹），避免误删有内容的目录。

#### 2.28 我的媒体库同步：修复「卡在正在获取数据」

**问题**：点「我的媒体库 → 媒体库同步」后，弹窗一直停在「**正在获取 XXX 数据...**」，进度条不走、不报错、结束不了；「统计」里电影/剧集数量始终是 0。同步 **Emby、Jellyfin、Plex 都会命中**。

**原因**：三件事叠加 ——

1. `get_items()` 在 Emby/Jellyfin/Plex 客户端里是**生成器**，而同步代码直接对它调用 `len()`，第一步就抛 `TypeError`；
2. 同步方法**没有兜底**，异常冒泡出线程后 `progress.end()` 永远不会执行，前端因此**永久停留在「正在获取...」**（这就是「卡住」的直接原因）；
3. 单条目抓取详情失败（接口超时/非 200）时函数隐式返回 `None`，下一行 `.get()` 立刻抛异常并被外层吞掉，**整个媒体库的循环当场中断**，后面的条目一条都不入库。

**修复后**：

- 同步**不会再卡死**：无论发生什么异常，弹窗都会结束并给出明确提示；
- 单条目失败只跳过该条并记日志，**不再连带丢失整个库**；
- **「动画电影」「综艺」「纪录片」等混合库不再被静默丢弃** —— 原先只识别 `CollectionType` 为 `movies`/`tvshows` 的库；
- 剧集下的 **合集（BoxSet）/ 季（Season）/ 目录**会继续向下展开，不再漏掉嵌套结构里的内容；
- 同名电影与剧集（如《三体》）不再互相误判为「已存在」，避免重复下载或漏下；
- 媒体库列表新增「**全选 / 全不选**」与「**已选 N / M 个库**」计数；一个库都没勾选时点「开始同步」会先确认 —— 此时的后端语义是**同步全部库**。

> 排查同类问题时可先看容器日志里有没有「媒体库同步异常终止」或「同步条目 xxx 出错，跳过」这两行。

#### 2.29 媒体库同步「响应速度」优化

**问题**：点首页「媒体库」打开同步弹窗要停顿一下才出现；点「开始同步」后进度条长时间停在「正在获取 XXX 数据...」不动，看起来像卡死；条目多时同步整体偏慢。

**原因**（前端 2 处 + 后端 2 处）：

1. 弹窗要等**两次请求**都返回才显示；
2. 「开始同步」后故意延迟 **1 秒**才建立进度流；
3. 后端把媒体库条目列表**一次性全抓完**（每条都要单独请求详情接口）才开始处理，所以进度条在整库抓完前纹丝不动；
4. 入库是**一条一提交**，机械硬盘 / NAS 上每秒只能写几条。

**修复后**：

- 点「媒体库」**立刻弹出**，状态稍后填充；点「开始同步」即刻开始刷新进度；
- 进度条**边取边推进**，实时可见；
- 入库改为**攒批提交**，同步明显变快；单条数据有问题时**只跳过那一条**，不会连带丢失整批；
- 媒体库列表移除「全选 / 全不选」按钮与提示文字，排版更干净（「已选 N / M」计数保留）。

> 若同步个别条目失败，日志会有「同步条目 xxx 出错，跳过」，不影响其余条目入库。

#### 2.30 「识别与搜索」区排版重排

**问题**：设置页「识别与搜索」一区里，开关与数值输入框混排在同一行，行高忽高忽低（36px 与 80px 交替）、两个数值框分散在上下两行、右下角还空出一块。

**修复后**：

- **9 个开关归为整齐的 4 列网格**，行高完全统一；窄屏自动降为 2 列；
- **两个数值框并排成一行**，各占一半宽，同类控件对齐；
- 功能、字段、保存逻辑**均未改动**。

#### 2.31 媒体服务器新增「飞牛影视」（v6.2.0）

**新增**：设置 → 媒体服务器 里多了一张「飞牛影视」卡片，填主机地址、用户名、密码即可接入飞牛影视（fnOS）。

**配置项**：

- **地址**（必填）：`http(s)://ip:port`，一般与飞牛影视 Web 访问地址一致；
- **外网播放地址**（可选）：跳转播放页面使用的地址；
- **用户名 / 密码**（必填）：飞牛影视登录账号；
- **访问码**（可选）：飞牛影视开启了「访问码」时必填，未开启留空；
- **同步媒体库**（可选）：全部 / 电影 / 电视剧，留空 = 全部；
- **扫描模式**（可选）：新添加和修改 / 补充缺失 / 覆盖扫描；
- **校验 SSL 证书**（可选）：使用自签名证书时可关闭。

**支持的能力**：媒体库浏览、电影 / 剧集 / 播放链接查询、媒体库刷新（触发扫描）、下载前控重、入库后刷新媒体库。

**说明**：纯 HTTP 客户端实现，**不引入任何新依赖**；与前四家（Emby / Jellyfin / Plex / 绿联影视）同级并列，可同时启用。

#### 2.32 飞牛影视首页与图片修复（v6.2.1）

**问题**：飞牛影视的「媒体库同步」能正常跑，但首页「我的媒体库」里看不到内容；封面图也全部不显示。

**修复后**：

- **首页恢复正常**：补齐「正在观看」与「最新入库」两个数据源，此前缺失会让整个首页加载失败；
- **封面图正常显示**：修正图片地址前缀，媒体库卡片也补上了封面；
- **统计数字正确**：电影 / 剧集数量不再恒为 0，剧集也会正确统计季集信息
  （**这一项同时修复了绿联影视的同类问题**）。

**说明**：修复均为补齐与放宽，**不影响 Emby / Jellyfin / Plex 的既有行为**，无新依赖。

#### 2.33 飞牛影视测试连接修复（v6.2.2）

**问题**：飞牛影视填好配置保存后，点「测试连接」始终失败。

**修复后**：

- **测试连接恢复正常**：修正了请求层的调用方式 —— 此前向工具类传入了它并不支持的参数，导致每个请求都会直接报错，功能完全不可用；
- **「校验 SSL 证书」开关真正生效**：此前该开关在飞牛客户端上是失效的；
- **封面图地址不再重复叠加前缀**；
- **未填「外网播放地址」时播放链接也能正常打开**。

**说明**：仅请求层实现变更，**不影响其它媒体服务器**，无新依赖。

**使用提示**：测试连接读取的是**已保存**的配置。（v6.2.3 起「测试」会自动先保存，v6.2.2 及以前必须先点「保存」的限制已解除，详见 2.34。）

#### 2.34 飞牛影视连接诊断与「测试即保存」（v6.2.3）

**问题**：飞牛影视点「测试」始终失败，页面上却只有一句「测试失败！」，看不出任何原因；有时重启容器后，填好的配置还会凭空消失。

**修复后**：

- **测试失败会直接告诉你原因**：不再只显示「测试失败！」，而是把具体原因弹出来 —— HTTP 状态码、返回的不是 JSON、业务错误码、连接被拒绝等；日志里也会带上 HTTP 状态码与响应片段；
- **点「测试」会先把配置真正保存下来**：此前点「测试」走的是「仅测试不保存」分支，配置只在内存生效、从不写入配置文件，容器一重启就回滚成空 —— 这正是「测试失败 / 首页媒体服务器连接失败 / 媒体库列表 0/0」的常见原因；
- **新增连接诊断脚本**，一条命令逐步检查地址、`/v` 路径、访问码、登录与媒体库列表：

  ```bash
  docker exec -it nas-tools python /nas-tools/scripts/diagnose_trimemedia.py
  ```

  不带参数时会自动读取已保存的「飞牛影视」配置。

**说明**：「先保存再测试」对**全部媒体服务器**生效（Emby / Jellyfin / Plex / 绿联影视 / 飞牛影视），更符合「填完就点测试」的直觉；无新依赖。

#### 2.35 飞牛影视：修复「测试连接」报 log.warning（v6.2.4）

**问题**：v6.2.3 点「测试连接」会弹出一句 `AttributeError: module 'log' has no attribute 'warning'` —— 这是 v6.2.3 引入的缺陷（日志函数名写错），而且它还会把**真正的失败原因覆盖掉**。

**修复后**：

- 报错消失；
- 地址探测失败时，会把「带 `/v`」和「不带 `/v`」两个候选地址**各自的失败原因都列出来**，例如：
  `所有候选地址均无法连接；http://192.168.3.3:5666/v → HTTP 404…；http://192.168.3.3:5666 → 连接被拒绝…`

**说明**：只影响日志调用与错误提示，不改变任何协议行为；已升级到 v6.2.3 的用户建议直接升到 v6.2.4。

#### 2.36 连接失败「看得见原因」+ 保存后回读（v6.2.5）

**问题**：媒体服务器连接失败时，首页只给一句「请确认Emby/Jellyfin/Plex配置是否正确」——
既没提你实际用的服务器（比如飞牛影视），也不给失败原因；诊断脚本也只在接口层，
一旦 DNS / 端口 / 证书 / 反向代理这条链断了，它只能报一句「请求异常」。

**改进后**：

- **首页按实际服务器报错**：文案改为动态名称 + 真实原因，例如
  `当前无法连接「飞牛影视」获取数据（媒体库服务器连接失败：无法连接 http://192.168.3.3:5666/v（连接超时））`；
- **诊断脚本新增四段前置体检**：DNS → TCP → TLS（含证书 / 自签名判定）→ 反向代理，
  体检不通过会给出可执行建议，并跳过无谓的接口等待：

  ```bash
  docker exec -it nas-tools python /nas-tools/scripts/diagnose_trimemedia.py
  ```

  想跳过体检直接打接口，加 `--no-preflight`。

- **保存后回读**：保存配置后会从磁盘重新读一遍校验，若「提交了却没落盘」
  （配置目录只读、磁盘写满等）会明确提示是哪些项。

**说明**：本版不改变任何协议行为、无新依赖。「连接失败」的成因可能在本机网络 / 地址侧，
本版的作用是把它指出来。

#### 2.37 飞牛影视封面恢复显示 + 标题显示名（v6.2.6）

**问题**：飞牛影视接通后，「我的媒体库」页的媒体库封面、「正在观看」「最新入库」的封面
**全部不显示**；页面标题还直接写着内部代号 `我的媒体库 - trimemedia`。

**原因**：飞牛的图片接口和业务接口一样要校验登录态，并且需要把登录凭证放进 Cookie
（`Trim-MC-token`）—— 而网页里的 `<img>` 标签**带不了请求头**，所以无论怎么拼图片地址都取不到图。

**修复后**：

- 图片改由 NAStool **本机中转代取**，并自动带上登录凭证（开了访问码时连同访问码凭证一起带），
  「媒体库列表 / 正在观看 / 最新入库」的封面都会正常显示；
- 标题按设置页里的名字显示：`我的媒体库 - 飞牛影视`；
- 诊断脚本新增**封面鉴权探测**，对同一张封面各请求一次「不带凭证 / 带凭证」，
  用状态码直接判断封面不显示是不是凭证问题：

  ```bash
  docker exec -it nas-tools python /nas-tools/scripts/diagnose_trimemedia.py
  ```

**安全说明**：中转图片时会校验目标地址与本机配置的**媒体服务器是否同源**（协议 + 主机 + 端口
完全一致），不是同一台就**不带任何凭证**，避免图片中转被当作「带着凭证访问任意地址」的通道。

**说明**：Emby / Jellyfin / Plex / 绿联影视不受影响（它们的图片不需要凭证，钩子默认返回空）。

#### 2.38 「媒体库同步」弹窗标题改显示名（v6.2.7）

**问题**：v6.2.6 改了「我的媒体库」的页面标题，但点「媒体库同步」弹出的那个完成弹窗顶部
仍显示内部代号 —— 飞牛影视显示为 `Trimemedia`、绿联影视显示为 `Ugreen`。

**原因**：同一个模板里有**两处**引用了媒体服务器类型。页面标题那处 v6.2.6 已换成显示名，
弹窗标题那处漏了，仍在用内部代号并套 `|title` 过滤器（`trimemedia` → `Trimemedia`，
看着像个人名，所以一眼看不出来）。

**修复后**：弹窗标题与页面标题口径一致，统一按「设置 → 媒体服务器」里的名字显示：

| 媒体服务器 | 改前 | 改后 |
|---|---|---|
| 飞牛影视 | `Trimemedia` | `飞牛影视` |
| 绿联影视 | `Ugreen` | `绿联影视` |
| Emby / Jellyfin / Plex | `Emby` / `Jellyfin` / `Plex` | 不变 |

**说明**：弹窗左侧那个图标**不受影响**（图标文件名本来就是内部代号，如 `trimemedia.png`），
本版只改文案。无协议、配置或依赖变更。

#### 2.39 媒体服务器图标统一走配置（v6.2.8）

**问题**：点「媒体库同步」后，弹窗左侧的图标对**绿联影视**用户显示为空白。

**原因**：该图标是按**内部代号拼文件名**的（`ugreen.png`），而绿联影视复用的是 `emby.png`
—— 磁盘上根本没有 `ugreen.png`，所以必然加载失败。Emby / Jellyfin / Plex / 飞牛影视
恰好都有同名图片，才一直没被发现。

**修复后**：图标改为与「设置 → 媒体服务器」同一口径，一律取配置里的图标地址：

| 媒体服务器 | 改前 | 改后 |
|---|---|---|
| 绿联影视 | 空白（`ugreen.png` 不存在） | ✅ 正常显示 |
| Jellyfin | `jellyfin.png` | `jellyfin.jpg`（与设置页统一） |
| Emby / Plex / 飞牛影视 | 正常 | 不变 |

**说明**：未新增静态资源、未改任何内部命名；无协议、配置或依赖变更。

#### 2.40 飞牛影视登录态失效自动重登（v6.2.9）

**问题**：NAS 或飞牛影视服务重启、登录会话过期、改了飞牛密码之后，NAStool 里飞牛影视这一路
就「哑了」—— 首页媒体库列表变空、封面不显示、「媒体库同步」失败或同步出 0 条，
而且**不会自己恢复**，只能重启容器。

**原因**：飞牛影视是登录型鉴权（token 有有效期），而 NAStool 只在**启动时**登录一次；
token 失效后每个请求都返回 401，旧代码只记日志、不会重新登录。

**修复后**：检测到登录态失效（401 / 403）时**自动重新登录，并把刚才那次请求重放一遍**，
页面无感恢复。可在「设置 → 媒体服务器 → 飞牛影视」里设置重登次数：

| 配置项 | 说明 |
|---|---|
| 自动重新登录次数 | 默认 **2** 次；填 **0** 表示关闭自动重登 |

三重保护，避免「自愈」变成新麻烦：

- 同一批请求 **30 秒内只重登一次**（批量同步时几十个请求会同时失效，否则会并发打爆登录接口）；
- 连续失败到设定次数就停止（密码填错时不会反复刷日志）；
- 停止后 **10 分钟**自动重开一轮（服务端临时不可用不会让自愈能力永久失效）。

**说明**：仅飞牛影视适用（Emby / Jellyfin / Plex 用长期密钥，绿联影视自带长会话保活）；
无新增依赖、无协议或数据结构变更。

#### 2.41 「种子管理模式」三种模式重做（v6.3.0）

**问题**：设置 → 下载器 → qBittorrent 里的「种子管理模式」，和它下面的「下载目录设置」
长期对不上：

- 选了「**自动**」，只要某一行填了「下载保存目录」，实际行为就会**退化成手动**；
- 没匹配到目录时会打一条 `没有可用的下载目录…` 告警 —— 但**只要那行配了分类标签，分类名
  其实已经下发给 qB 了**（qB 会按该分类的保存路径落盘），这条告警纯属**误报**；
- 选了「**默认**」，NAStool 照样会去创建 / 覆盖 qB 的分类与保存路径。

**修复后**：三种模式严格各管一段，界面也随模式自动调整。

| 模式 | 下发给 qBittorrent | 说明 |
|---|---|---|
| **默认** | 什么都不下发 | 完全沿用 qBittorrent 自身的设置与行为，NAStool 不做任何干预 |
| **手动** | 下载保存目录（不下发分类） | 下载目录由 NAStool 决定 |
| **自动** | 有分类 → 只下发分类；没分类 → 下发下载保存目录 | 有分类时由 qB 按该分类绑定的保存路径落盘；两者都没有则交给下载器默认路径 |

**告警也按模式给**：「默认」模式**不再告警**（那本来就是「沿用下载器设置」）；
「手动」模式没匹配到下载保存目录才告警；「自动」模式分类与目录都没有才告警。

**界面**：「下载目录设置」随所选模式调整 —— 默认模式整表收起（此时它只用于**路径映射**与
**自动分类**，不影响下载位置）；手动模式不再显示「分类标签」；自动模式全部显示。
另外**只有「自动」模式**会在启动时按「下载目录设置」创建 / 更新 qB 分类。

**说明**：非 qBittorrent 下载器（Transmission 等）没有「种子管理模式」这个概念，行为不变；
刷流 / IYUU / 订阅等显式指定下载目录的链路也不受影响。

**顺带修掉一个空状态报错**：**未配置任何下载器**时打开「设置 → 下载器」，页面会直接报错（500）
—— 因为该页面在「没有下载器」的分支里用了 `OOPS.empty(...)`，却漏了一行模板 import
（全仓其它 17 个用到它的模板都写了，只有这一处漏）。现已补上。

#### 2.42 「文件管理 → 转移」不再只转一部分（v6.3.1）

**问题**：在「文件管理」页面点顶部的「转移」整理当前目录时，如果目录里的文件**有任何一个**
出问题（识别不出媒体信息、文件名里读不出季集、转移动作失败…），**整批就会停在那一个文件上**，
后面的文件一个都不处理。界面上明明写着「共 26 个文件」，最后只转移了 5 个，而且**没有任何提示**。

**修复后**：

- 单个文件出问题**只跳过它自己**，其余文件继续按规则逐个识别与转移；
- 结果提示改成带数量的汇总：**共 N 个文件，成功 X 个，未转移 Y 个（原因）**；
- 在识别阶段就被跳过的文件会登记到「未识别」页面，可到那里手动处理。

**说明**：目录里小于「基础设置 → 媒体库 → 最小文件大小」的文件本来就不会被处理（这是配置规则）；
刷流 / IYUU / 订阅等自动整理链路的单文件失败行为也一并变得「不再牵连其它文件」。

#### 2.43 关闭「增强识别V2」后，`[片名]` 开头的中文资源也能识别对了（v6.3.2）

**问题**：在「基础设置」里**关闭**「增强识别V2」之后，片名写在方括号里的文件会识别错误 ——
`[诛仙].Jade.Dynasty.2024.S02E01.2160p.WEB-DL.H265.AAC-AilMWeb` 被认成 **`Dynasty`**，
`[斗罗大陆][第105集][1080p].mp4` 甚至**认不出片名**（点「识别」只得到「无法识别」）。
打开「增强识别V2」时一切正常。

**原因**：关闭「增强识别V2」时用的是另一套识别器，它会把文件名开头的第一个 `[...]`
**无条件删掉**。片名写在方括号里（`[剧名][集数][分辨率]`，中文资源里很常见）时，
等于把片名一起删了。

**修复后**：方括号里是**片名**就保留、是**发布组 / 字幕组**（`[VCB-Studio]`、`[喵萌奶茶屋]`…）
才删掉，识别结果与打开「增强识别V2」时一致。**打开「增强识别V2」的用户不受影响。**

#### 2.44 文件转移「只处理了几个」的根因：最小文件大小；新增「测试」预检（v6.3.3）

**问题**：在「文件管理」里点「转移」，界面显示目录里有 26 个文件，最后只转移了 5 个，
而且「失败 0」、没有任何提示。

**原因**：「手动识别」弹窗里的「最小文件大小」**留空**时，会悄悄套用
「基础设置 → 媒体库 → 转移最小文件大小(MB)」（默认 150MB）。小于这个值的文件
**在扫描阶段就被丢掉** —— 既不算成功、也不算失败，所以看不到任何提示。
（v6.3.1 已经把「识别阶段」被跳过的文件记账了；这次补上「扫描阶段」这最后一个口子。）

**修复后**：

- 「最小文件大小」**默认填 0**（不限制大小），需要限制时自己填；
- 「转移」旁边新增**「测试」按钮**：只统计不转移，直接给出
  **可识别 N 个 / 预计转移 M 个**，以及目录文件数、扫描通过数，
  和被过滤 / 被忽略 / 识别不出的明细与原因；
- 扫描阶段被丢弃的文件会写进日志（数量 + 文件名 + 原因）。

**建议**：参数不确定时，**先点「测试」再点「转移」**。

#### 2.45 绿联影视「测试连接」失败时会显示真实原因了（v6.3.4）

**问题**：绿联影视点「测试」，只显示一句笼统的「测试失败！」，看不出是地址错、
端口错、密码错，还是绿联系统升级后接口变了。

**原因**：页面本来就支持把客户端记录的失败原因显示出来（飞牛影视一直在用），
但绿联客户端**从未实现**这个字段，失败原因只写进了容器日志，
页面上就只剩一句「测试失败！」。

**修复后**：点「测试」会直接显示真实原因，例如

- 「请求 `http://192.168.1.10:9999/ugreen/v1/verify/check` 无响应或异常
  （`ConnectionError: ... [WinError 10061] 由于目标计算机积极拒绝`）」
  —— **地址或端口不对**；
- 「返回非 JSON 响应（HTTP 200 … Content-Type：text/html，响应：`<html>…`）」
  —— **端口连到了别的服务**（反代 / 别的网页）；
- 「获取登录公钥失败：用户名不存在」—— **用户名不对**；
- 「登录失败：密码错误」—— **密码不对**。

排查顺序：**先看这条原因**，再决定改地址、改端口、改账号还是改密码。
另：登录请求补了 `UG-Client-Id` 头，兼容新版绿联登录客户端标识。

#### 2.46 绿联影视媒体库同步「数量 0」修好了（v6.3.5）

**问题**：绿联影视「测试」已经通过，但点「开始同步」只显示
**「同步数量：0」** —— 媒体库列表能看到 6 个库，条目却一条都拿不到。

**原因**：绿联的媒体库接口**不返回媒体库路径**，而旧实现把「路径」当成遍历入口，
于是每个库在第一道门槛就退出了（返回空**不报错**，所以看起来像「同步成功」）。

**修复后**：改为按媒体库 ID 直接取全量条目再本地分库，实测 6 个库共 **956 条**
全部入库：

| 库 | 条数 |
|---|---|
| 电影 | 451 |
| 动漫电影 | 17 |
| 儿童电影 | 365 |
| 电视剧 | 85 |
| 动漫电视剧 | 17 |
| 儿童电视剧 | 21 |

顺带修掉几个「同一原因造成的空白」：**下载前查重**（失效会导致重复下载）、
首页「**继续观看**」与「**最近添加**」卡片（之前一直是空的）。

另外还修掉一处会让同步**悄悄少几条**的坑：绿联的条目接口在按「最近播放 / 最近更新」
排序翻页时，相邻页会**重复返回同一条**，结果有 6 部片被挤出所有页、永远取不到，
而接口报的总数还是满的 —— 从日志上完全看不出来。现在改走稳定排序，并拿接口报的
总数做校验：万一仍取不全，会**换另一种排序补拉**，日志里直接写明「仍缺 N 条」。

**为什么要点一下「开始同步」才知道**：同步是在你点按钮时执行的，升级镜像后
需要重新点一次「开始同步」才会把条目写进库。

#### 2.47 绿联影视「查剧集 / 下载查重」查不到的问题修好了（v6.3.6）

**问题**：媒体库同步已经正常（v6.3.5 修好），但还有两处查不到东西：

- **剧集**：查询「已经有哪些集」时总是报**整季一集都没有** —— 已经下完的剧会被
  认为缺集，可能重复下载整季；
- **电影**：下载前查重认为**媒体库里没有**这部片 —— 已入库的电影会被重复下载。

**原因**：旧实现有两条死路 ——

1. 用**接口的搜索参数**去库里找片名，而绿联这个参数在服务端**被忽略**
   （传什么关键词都返回同一批默认条目）；
2. 读**并不存在的返回字段**：剧集的真实返回里，集列表叫 `tv_info`、季列表叫
   `season_info`，旧代码读的 `episodes` 字段在真实返回里根本没有。

**修复后**：改为**在本地全量条目里按片名匹配**（复用同步时已拉取的索引，有缓存、
不额外加请求），并读真实字段取季 / 集。

多季剧也能正确区分了：绿联把每一季做成一条**独立条目**（「某某 第 1 季」
「某某 第 2 季」，各季年份还可能不同），现在按「第 N 季」定位，查第 2 季不会再
返回第 1 季的集；同时「年份」改为**软条件** —— 之前按首播年精确过滤会把第 2 季
整条滤掉，除首季外全部误报「一集都没有」。

**为什么要点一下「开始同步」才知道**：查重与缺集判定都依赖本地那份条目索引，
升级镜像后需要重新点一次「开始同步」把索引建起来。

#### 2.48 绿联影视的封面图终于显示了（v6.3.7）

**问题**：打开 **「我的媒体库 - 绿联影视」**，六个媒体库卡片上方全是**黑色方块**；
点进库里的**条目封面**、「正在观看」「最近添加」的小图，同样都显示不出来。

**原因**：绿联的图片接口要求带登录凭证，而浏览器的 `<img>` 标签带不上，
所以这类图片要**由 NAStool 代取再转发**给浏览器。而转发时用了两个错：
① 请求的**接口地址写错了**（少一个字母，指向一个并不存在的接口）；
② **没有带上登录凭证**。
绿联对这种请求会回一段「授权失败」的**文字**，浏览器把它当图片解析失败
⇒ 就显示成黑块了 —— 注意这时 HTTP 状态码其实是 200，所以不报错，只是画不出图。

**修复后**：改用正确的接口地址，并把登录凭证随请求带给绿联，封面全部恢复。

顺带修好了另一件事：「我的媒体库」卡片过去**从来就没有封面** ——
绿联的媒体库列表接口其实**自带**每个库的海报，只是旧代码没取。
现在优先用绿联自带的海报（**本地文件优先**，因为有些库的海报是云端地址、
带时效签名、过期会失效），某个库若没有可用的本地海报，
自动改用**该库目录下的封面图**兜底，保证六个卡片都有图。

> 封面地址在页面渲染时生成，若以后登录态变化导致图片失效，**刷新页面**即可恢复。

#### 2.49 「蜜柑」搜不到番剧的问题修好了（v6.3.8）

**问题**：用片名搜番剧时，**蜜柑**这个站点一条结果都出不来，日志还会提示
「页面包含登录表单（name="username"），Cookie 可能已失效」。
但蜜柑是**公开站、根本不用登录**，这个提示是**误报**。

**原因**：蜜柑**改版了** —— 搜索结果的外面多包了一层容器、每行前面又多了一个勾选框。
NAStool 按旧结构去找结果行，一条也找不到；找不到之后，又被页面里那个
**可选的登录入口**（页头页脚各一个，字段名 `UserName` 大小写不同但被当成同一个）
误判成了「Cookie 失效」，于是排查方向被完全带偏。

**修复后**：抓取规则改成按网页的**类名**定位，不再依赖层级和列序号 ——
现在搜「诛仙」能正常取回结果（一次最多 100 条）。

顺带加了一层保护：**以后这个站点（或任何内置站点）再改版**，提示会变成
「该站结构可能已改版，需更新索引器定义」，**不会再误报成 Cookie 失效**，
省得再去白折腾登录态。

> 说明：这条修复对所有内置站点生效 —— 有正常内容页的站点，不会因为页面上
> 存在可选的登录入口就被判成登录页。

#### 2.50 订阅：能看到还缺哪几集，补齐之后才自动退订（v6.4.0）

**能看到缺哪几集了**：电视剧订阅卡片上会显示「缺 N 集」并列出集号
（缺得多时悬停可以看到完整清单），点开媒体详情弹窗还能看到完整的缺失集列表。

**什么时候自动退订**：以前是「**这一轮搜到资源就退订**」——
种子刚交给下载器、还没下载完（甚至下载失败）时，订阅就已经被清掉了，
剩下没补齐的集再也没人管。现在改为**等媒体库真的查到这部剧已经完整**才退订；
在此之前订阅会一直保留，并按周期（默认最多 6 小时一轮）自动重试。

**顺带的好处**：订阅保留期间，每一轮只针对**媒体库实际还缺的集**去搜索，
已经入库的集不会再被重复提交；媒体库一时连不上时也会跳过本轮、保持订阅，
**不会**被误判成「已经补齐」而把订阅清掉。

> 说明：`补齐` 的判断依据是**媒体服务器（Emby / Jellyfin / Plex / 飞牛 / 绿联）
> 或媒体库目录**。如果两者都没有配置，缺集信息会退化为按目录扫描，
> 建议至少配好其中一个。

#### 2.51 老订阅也能看到缺哪几集了（v6.4.1）

**现象**：升级前就已经存在的订阅，卡片上会显示「缺 26 集」，但点开详情列不出具体集号。

**原因**：这类订阅数据库里只存了缺集**数量**，没存集号（集号要等订阅扫描时才写进去）。

**现在**：只要这部剧是「整季都缺」，就直接按「第 1 ~ 第 N 集」列出来，并标注
「整季缺、待扫描确认」；等下一次自动扫描（默认最多 6 小时一轮）写入真实集号后，
标注会自动消失。如果只是缺了一部分、数据库里又没有集号，界面会如实只显示数量，
**不会编造集号**。

> 说明：本次只改界面展示，不影响搜索、下载与「补齐后自动退订」的判定。

#### 2.52 绿联季名两种写法都认 + 缺集明细标明季（v6.4.2）

**现象**：绿联影视里明明有这部剧（如「诛仙」第 1~3 季都在），订阅却显示「缺 26 集」，
而且反复重新下载；点开详情又看不出说的是哪一季。

**原因**：绿联媒体库里「季」的写法有两种并存 —— `诛仙 第 1 季` 和 `诛仙 季 1`。
本版之前只认前者，于是用后一种写法的剧（实测某真实库里 123 部剧中有 47 部）会被当成
「媒体库里没有这部剧」，该剧的每一季都被判成「全集都缺」。

**现在**：两种写法都认，缺集数量按真实媒体库计算；缺集明细写成
「**第 4 季** · 缺失剧集（共 26 集）：第 1 集 …」，一眼看出是哪一季。
订阅列表的卡片不再显示缺集明细，只在点开详情后展示，列表保持整齐统一。

> 顺带修了一个隐患：绿联查询不可用（登录失败 / 连接异常）时，不再被当成「已经全都有了」，
> 避免把还没补齐的订阅当成已完成而**自动退订**。

#### 2.53 飞牛/绿联「查不到剧」时不再误报缺集、不再误退订（v6.4.3）

**现象**：飞牛影视里明明有这部剧，订阅却报「整季都缺」并反复重新下载；更糟的是，
飞牛连不上（登录失效 / 网络异常）时，还没下完的订阅会被当成「已经下完了」**自动退订**。

**原因**：订阅检查会拿剧名和年份去媒体库里比对，飞牛要求**名字与年份完全相等**才认 ——
差一点就被当成「库里没有这部剧」，该季每一集都判成「缺」。而查询失败时返回的是一个
**空结果**，上层把它读成「一集都不缺」，于是订阅被误判完成并清除。

**现在**：这两种「拿不准」的情况都会如实表示**无法确认**，改由本地目录扫描复核，
既不凭空报「整季全缺」，也不会误退订。绿联影视的电影查重也一并对齐。

> 只改「查不到的时候怎么回答」，不动读取季集的逻辑本身；能正常查到的媒体库，结果与上一版一致。

#### 2.54 缺失集只算到「已播出」，连载中的季不再把没播的算进去（v6.4.4）

**现象**：诛仙第 4 季在数据源里标着 26 集、实际只播到第 9 集，订阅详情却写着
「缺失剧集（共 26 集）：第 1 集 … 第 26 集」，把 17 集**还没播**的也算成了缺失。

**原因**：整条链路只知道「总集数」（该季排定的总集数，含已排期和尚未播出的），
没有「已播出」这个概念，于是缺集一律从第 1 集算到总集数。

**现在**：连载中的季**只把已播出的集**算作缺失，旁边一并给出本季播出进度
（例如「共 9 集，本季已播出 9 / 26 集（连载中）」）；已播出的集都入库后，
弹窗会提示「已播出的集已全部入库」而不是留空。某一季已经播完时，结果与上一版完全一致。

> **不影响「补齐后自动退订」**：卡片进度条（已入库 / 总集数）和订阅完成判定都仍按
> **总集数**计算 —— 已播出的集下齐了**不会**被当成「整季完成」而退订，后续新集照常继续下载。

#### 2.55 刷流时不再重复下载库里已有的片子（v6.4.5）

**现象**：刷流选种时，明明是媒体库里早就有的同一部片子，却还是被选中下载，
白白占带宽、重复入库。

**现在**：刷流任务的「选种规则」区多了一个「跳过已入库」开关。勾上以后，刷流在选种
阶段会先查你自己的**媒体库目录**——电影按「片名 + 年份」比对，剧集**精确到季 / 集**
（库里已有 S04E01–E09 就只跳过这几集，缺的集照常下载）。库里已经有的直接跳过，不再下载。

- 判断**只看本地媒体库目录**，不额外联网；
- 季号写法不一致时先跟 TMDB 对齐一次再比对，避免「发布方第四季 / 库里第三季」这类错位误判；
- 默认**关闭**，只对勾选的任务生效；媒体库没配好或读取出错时**自动放行**，绝不误拦。

> **顺带把「包含 / 排除」合成一个框**：原来的两个输入框现在合并成一个，用 `T=` 开头表示
> 包含、`F=` 开头表示排除。例：`T=1080p F=预告`；多个条件用 `|` 连接，
> 如 `T=1080p|2160p F=预告|花絮`。不写前缀时按「包含」处理，与旧用法兼容；
> 编辑旧任务会自动拼成新写法回显，保存后原值不变。

#### 2.56 刷流弹窗版式微调：包含/排除拆回两框、「跳过已入库」上移、限速下线（v6.4.6）

v6.4.5 上线后按实际使用反馈做了三处调整：

- 「**包含**」「**排除**」**拆回两个独立输入框**（上一版合并成 `T=` / `F=` 单框，用起来不如分开直观）；
- 「**跳过已入库**」**挪到弹窗顶部开关行**，与「消息推送」「转移到媒体库」「限免到期删种」并列，更醒目；
- 「**限免到期下载限速**」开关**下线** —— 限免快结束时不再自动把下载速度压到 1 B/s。
  若想在限免结束时立刻止损，仍可用「限免到期删种」。

选种规则区回到 **9 个卡片、3×3 整行对齐**（促销 / Hit&Run / 任务总数 ｜ 大小 / 做种数 / 发布时间 ｜
年份 / 包含 / 排除）。

#### 2.57 英文片名不再被误削、重复中文名不再拼接（v6.4.7）

- **`Jade Dynasty` 这类英文片名不再被误识别**：以前 `Jade` 会被当成香港 TVB 翡翠台的台标剥掉，
  片名只剩 `Dynasty`，结果匹配到 1981 年的美剧《豪门恩怨》。现在只有 `Jade` 后面**紧跟中文剧名**时才剥台标
  （`JADE.爱回家` 这类港剧照旧），英文片名完整保留。
- **`[剧名]剧名…` 这类文件名也能识别了**：方括号里的中文名与后面重复时，以前会被拼成「剧名 剧名」而匹配不上 TMDB，
  现在重复的中文名不会再被拼接。

> 这一版修好之后，之前为绕开该问题添加的「自定义识别词」可以删掉（设置 → 自定义识别词），
> 两种命名（中文 + 英文 / 纯英文）都能正确识别。

### 3. 跳转与入口优化

方便把 NAS-Tools 当作媒体管理主入口：

- 下载管理 → 正在下载：可直接跳转下载器查看种子详情；
- 我的媒体库：标题可跳转对应媒体库；
- 索引器：Jackett / Prowlarr 可跳转各自服务；
- 媒体服务器：每个服务器配置界面可直接跳转该服务。

### 4. 飞书消息通知（双向交互）

基于 `lark-oapi` 的 WebSocket 长连接接入飞书自建应用，**无需公网 IP、域名或内网穿透**：

| 能力 | 说明 |
|---|---|
| 通知推送 | 下载、入库、订阅、签到、刷流、站点消息、媒体服务器等全部推送开关 |
| 交互式搜索 | 在飞书中直接发送关键字搜索站点资源，回复序号即可下载或订阅 |
| 管理命令 | 支持 `/` 开头命令，支持用户白名单与管理员白名单（基于 `open_id`） |
| 卡片消息 | 自动上传并展示海报图片，附带「查看详情」跳转按钮 |

配置步骤见 [飞书机器人配置与使用](#飞书机器人配置与使用)。

#### 4.1 已落地的体验改进

飞书的用户 ID（`open_id`）与群 ID（`chat_id`）**在飞书客户端界面和开放平台里都看不到**，
用户无从获取，配置项只能空着，于是点「测试」必然失败。针对这条链路做的改进：

| 改进 | 解决的问题 |
|---|---|
| 被拒绝时回复对方自己的 `open_id` | 正面解开「拿不到 ID → 填不进去 → 一直失败」的死锁 |
| 「测试失败」提示改为两步自举指引 | 原先只显示「测试失败！」，原因藏在日志且信息原始 |
| 接收者 ID 按前缀自动识别 | `ou_`→用户、`oc_`→群、`on_`→union_id、含 `@`→邮箱；群 ID 误填进用户列表也能正确发送 |
| 配置阶段即校验 ID 格式并写日志告警 | 填错不再等到发送阶段才报错 |
| 飞书错误码附带排查建议 | `230013` / `230002` / `230098` / `99991663` / `10003` 等直接翻译为可操作建议 |
| 配置项标题改为飞书原生叫法 | `群 Chat ID` / `用户 Open ID` / `管理员 Open ID`，tooltip 写明获取步骤 |

#### 4.2 实现要点

- **接收**：`lark.ws.Client` 长连接跑在独立 daemon 线程；事件回调**只做解析与转发**（转到本机 `127.0.0.1` 回环接口），耗时的搜索 / 下载交给后台线程——飞书要求事件在 **3 秒内**响应，超时会触发重推，导致同一条命令被执行两次；
- **未注册事件兜底**：`lark-oapi` 对未注册的事件会抛异常并返回 500，同样会引发飞书反复重推；已对 12 个兄弟事件做空实现兜底注册；
- **发送**：`im/v1/messages` 卡片消息，支持 `lark_md` 文本、海报图片（先传 `im/v1/images` 换 `image_key`，带 LRU 缓存）、跳转按钮；
- **卡片标题不重复**：正文与标题同源的消息（AI 应答、各类通知）**不再同时显示标题栏和正文**，内容只出现一次；「标题 + 正文」的消息仍保留标题栏 + 正文的卡片结构；
- **Token**：`tenant_access_token` 缓存复用，按 `expire - 600s` 提前刷新；
- **降级**：未安装 `lark-oapi` 时接收失效但发送照常，日志会提示安装；
- **安全**：本地回环接口仅允许 `127.0.0.1` / `::1` 访问。

### 5. 备份与恢复条目化

- 「服务 → 备份&恢复」支持**按条目勾选**备份与恢复，覆盖基础设置、二级分类策略、站点、刷流任务、下载器与同步目录、订阅与过滤规则、用户与权限、消息渠道、系统设置项、媒体库同步数据等；
- 新增**历史与统计记录**条目（默认不勾选）与**其它数据**兜底条目（版本升级新增的表自动归入），保证备份始终完整；
- 恢复时先解析备份内容、只列出文件中实际包含的条目（含条数与备份版本/时间），再选择性恢复：**勾选项精确覆盖、未勾选项不受影响**；
- 数据库按表恢复而非整体替换 `user.db`，保留数据库版本记录，并兼容旧版本生成的备份文件。

### 6. AI 助手（消息远程控制）

消息渠道（飞书 / 微信 / Telegram 等）中的对话不再只是闲聊——开启后 AI 可以**直接查询并操作 NAStool**。

而且你**不需要知道它有哪些功能，也不需要把话说对**：信息不全它会问，不知道说什么它会列菜单。

#### 6.1 三种处理方式

| 你的输入 | 它的判断 | 它做什么 |
|---|---|---|
| 意图清楚、信息齐全<br>`帮我订阅沙丘 2021` | 可以直接干 | **直接调用功能**，不多问一句 |
| 缺少关键信息<br>`帮我订阅`（没说哪部） | 需要补全 | **主动追问**「要订阅的是哪部片子？」，你回答后接着执行 |
| 不知道要说什么<br>`你好` / `你能做什么` / 随口一句 | 需要引导 | **列出能力清单**（按分组），邀请你说需求；与该用户的首次对话会自动介绍一次 |

**它不会做的事**：不会把话说一半就动手（缺参数一定先问），也不会凭空编造数字和列表（状态类问题必须查真实数据）。

#### 6.2 交互流程

```
用户发来一句话
      │
      ▼
┌─────────────────────────────┐
│ 有未结清的事吗？             │  待确认（危险操作已提问）
│                             │  待补全（已追问、等你回答）
└──────────┬──────────────────┘
     有    │    无
     ┌────┘    └──────────────┐
     ▼                        ▼
 先结清                    意图识别
 确认→执行                 ┌────┼────────────────┐
 补全→执行                 ▼    ▼                ▼
                      信息齐备  信息不足      无法判断意图
                          │        │              │
                     直接调用功能  主动追问      列出能力清单
                          │     缺的那一项        │
                          │        │              │
                          └──► 结果 ◄──── 用户回答/挑选 ──┘
                                │
                        提炼成简洁中文回复
```

**六个值得注意的细节**：

1. **一次只问一件事** —— 缺多个参数时合并成一句话问完，绝不来回拉扯；
2. **追问有记忆** —— 你回一句「沙丘」，它知道这是在补上一个问题，不会当成新任务重新理解；**追问期间你的回复一定交给 AI**，不会被「搜索 / 订阅」这类关键词截走；
3. **追问有边界** —— 10 分钟未回答自动失效；回「取消 / 算了」即放弃；你直接换个话题，它也会放下待办跟着你走；
4. **危险操作仍要确认** —— 删除、重启、升级、清空历史会先给确认提示，**回复「确认」才执行**（与追问互不干扰）。
5. **只发人话，不发 JSON** —— 模型偶发把自己的内部调用写成 JSON（本地小模型与部分中转更常见），系统会拦下这类输出，改发一句可行动的话，**绝不把原始 JSON 发进你的聊天窗口**。
6. **搜片不问链接** —— 你只发一个片名、或者说「想看某部片」，它直接发起资源搜索、把可下载的结果推给你挑；不会只丢一段简介了事，更不会反过来问你「链接是什么」——手上没有链接，本来就是它该替你解决的事。

#### 6.3 触发条件一览

| 触发场景 | 条件 | 行为 |
|---|---|---|
| 直接调用 | 意图可识别 **且** 必填参数齐全 | 调用工具完成操作，不追问 |
| 主动追问 | 意图可识别，但**缺必填参数**（片名、站点名、任务ID、季集等） | 抛出一个具体问题（能配话术就用人话，否则带上参数说明） |
| 列出能力 | 用户问「你能做什么 / 有什么功能 / 帮助 / 菜单」，或消息无法识别意图、仅为问候 | 输出分组能力清单 + 每种能力的示例说法 |
| 首次介绍 | 该用户在本会话的**第一次**发言（或 `#清除` 后重新开始） | 回答前附一段简短自我介绍，并引导说需求 |
| 二次确认 | 命中**危险工具**且开关开启 | 给出确认提示，回复「确认」才执行，5 分钟有效 |
| 强制接管 | 开启「AI优先接管」开关 | 所有文本消息（含以「订阅/搜索/下载」开头的）优先交给 AI |
| 追问续接 | 该用户存在未过期的待补全 | **无论内容以什么开头**，都交给 AI 续接 |
| AI 不可用 | 未填 API Key、关掉「AI 助手」开关，或 AI 调用失败 | 消息按本地关键词规则处理（搜索 / 订阅 / 下载链接），与没有 AI 时一致 |

> 「AI优先接管」默认**关闭**：此时以「订阅 / 搜索 / 下载」开头的消息仍走原来的关键词规则，其余消息交给 AI。
> 打开后，原来的关键词指令由 AI 等效完成（AI 内部调用的是同一套功能），适合想完全用自然语言操作的用户。

> **两个前提都要满足**：填了 API Key，**并且**打开「AI 助手」开关。只填 Key 而不打开开关时，
> 消息一律按关键词规则处理，与没有 AI 时一致——开关就是 AI 接管的唯一入口。
>
> **AI 不可用会让路，不会卡住你**：网络不通、接口报错、Key 失效时，机器人会先发一句
> 「AI 助手暂时不可用：…，已切换为本地模式处理」，然后把这条消息按关键词规则再走一遍。
> 「搜索 片名」「订阅 片名」、只发片名、直接发磁力链都照常可用。

#### 6.4 可调用功能的接入范围

共 **48 个工具**，全部通过 `WebAction().action(命令, 参数)` 调用既有功能，**不重复实现业务逻辑**，
因此 AI 操作与网页端点击的行为完全一致。

| 分组 | 数量 | 覆盖内容 |
|---|---|---|
| 下载管理 | 8 | 下载中/已完成任务、下载器状态与下载目录设置、磁力链接直下、暂停/继续/删除任务 |
| 订阅与搜索 | 10 | 电影与剧集订阅、订阅历史、影片资料查询、上映日历、添加/删除/刷新订阅、重新订阅、搜索站点资源 |
| 站点 | 6 | 站点列表与状态、站点流量数据、单站活动趋势、做种分布、站点最新资源、索引器 |
| 媒体库 | 8 | 入库历史与统计、磁盘空间、影片数量、播放记录、未识别文件、重新识别、媒体库同步 |
| 刷流与同步 | 5 | 执行刷流任务、目录同步任务列表与执行、自动删种任务与执行 |
| 历史与清理 | 3 | 清空整理/订阅/黑名单历史、删除单条订阅历史、清空未识别记录 |
| 系统运维 | 6 | 系统负载、版本检查、备份条目查看、清理元数据缓存、重启服务、系统升级 |
| 使用帮助 | 2 | 能力清单、向用户提问（内部工具，不出现在菜单） |

按副作用分级，**只有标记为危险的操作需要二次确认**：

| 级别 | 数量 | 说明 |
|---|---|---|
| 只读查询 | 26 | 无副作用，不写审计日志 |
| 写操作 | 14 | 订阅、下载任务控制、重新识别、媒体库同步等；写审计日志 |
| 运维操作 | 6 | 删种、清历史、重启、升级；写审计日志 + **二次确认** |
| 元能力 | 2 | 列能力、提问题；不触碰系统 |

**明确不在接入范围**（有意排除，不是遗漏）：

| 排除类别 | 代表功能 | 原因 |
|---|---|---|
| 配置修改 | 站点配置、下载器配置、通知渠道、媒体服务器、分类策略、系统设置 | 参数是嵌套结构体，自然语言无法可靠表达；改错影响全局 |
| 用户与权限 | 用户增删改、权限分级 | 安全边界，应由人在界面上操作 |
| 插件管理 | 插件安装/卸载、插件配置 | 涉及代码执行，风险不可控 |
| 批量结构化数据 | 自定义识别词、过滤规则编辑与导入 | 结构化批量数据，适合界面表格操作 |
| 备份与恢复执行 | 上传备份文件并恢复 | 需文件上传/下载、覆盖范围大（仅保留「查看可备份条目」） |
| 文件系统操作 | 目录浏览、文件重命名、删除文件、硬链接查找 | 直接操作磁盘路径，不可逆且难以一句话说清 |
| 登录态 | 登出、重置数据库版本 | 账号层面操作，与「管家」职责无关 |

#### 6.5 配置项

（设定 → 基础设置 → **实验室** → 「AI 助手」分区）

实验室卡片内按 **三个分区**排列，互不混淆：

| 分区 | 内容 |
|---|---|
| **Telegram Bot API 代理** | 只影响 Telegram 机器人的反代地址，**与 AI 助手无关** |
| **AI 助手** | 本节所有配置，外加一项 AI 应用开关「AI 识别文件名」（接入官方 / 第三方中转 / 本地模型都改这里） |
| **识别与搜索** | 媒体识别与搜索策略开关，同样与 AI 助手无关 |

所有选项统一四列对齐（窄屏自动堆叠），标签与控件同行同高。

| 配置项 | 默认 | 说明 |
|---|---|---|
| OpenAI API Url | 官方地址 | 留空使用 `https://api.openai.com`；接本地模型时填本地推理服务地址。末尾带不带 `/`、带不带 `/v1` 都会自动按 `/v1` 处理 |
| OpenAI API Key | 空 | **必填**，留空则 AI 能力整体不可用；接本地模型时随便填一个非空值即可 |
| OpenAI 模型 | `gpt-3.5-turbo` | 接入本地模型 / 第三方中转时填对应模型名 |
| **测试连接** | — | 按钮：用**当前填写的内容**（无需先保存）实测一次，明确反馈成功或失败 |
| AI助手 | **开** | 关闭后退回纯聊天（行为同旧版） |
| 引导式询问 | **开** | 信息不足或不知能做什么时主动追问 / 列能力清单；关闭后信息不足只提示缺什么 |
| AI优先接管 | 关 | 所有文本消息优先交给 AI（含以「订阅/搜索/下载」开头的）；关闭时这些消息仍走关键词规则 |
| 危险操作需确认 | **开** | 关闭后 AI 可直接执行危险操作 |
| 显示工具调用 | 关 | 在回复中标注本次调用了哪些工具，便于排查 |
| AI 识别文件名 | 关 | 识别不出媒体信息时，把文件名交给上面配置的 AI 服务读一遍，再拿结果回查 TMDB。**与 AI 助手共用同一份配置，无需单独申请 API**；未填 API Key 时此开关无效。详见 [6.6](#66-ai-识别文件名) |

**「测试连接」会返回什么**：

| 结果 | 含义 |
|---|---|
| 连接成功 ｜ 模型 xxx ｜ 耗时 xxx ms ｜ 支持原生工具调用 | 地址通、鉴权通过、模型可用，可直接使用 |
| 连接成功 ｜ … ｜ 不支持原生工具调用，将自动降级为文本协议 | 后端未实现 function calling，AI 仍可用（效率略低） |
| 连接失败：API Key 无效或未授权（HTTP 401） | Key 错误或已失效 |
| 连接失败：接口地址不存在（HTTP 404） | API Url 填错，或该地址不是 OpenAI 兼容接口 |
| 连接失败：请求被拒绝（HTTP 400）：The model … does not exist | 模型名不存在，多半是模型名拼错或后端没加载该模型 |
| 连接失败：无法访问 xxx（Connection refused） | 地址/端口不通，或服务未启动 |
| 连接超时：N 秒内没有收到响应 | 地址可达但推理服务未就绪（模型还在加载） |

测试**不会修改任何配置**，也不会影响正在运行的 AI 助手。

代码级参数（不提供界面，需要时直接改 `config.yaml` 的 `openai` 段）：
`agent_max_rounds`（最大工具调用轮数，默认 5）、`agent_protocol`（工具调用协议，默认 `auto`）。

**操作审计**：所有非查询类操作都会写入系统消息（界面「系统消息」与日志可见），可追溯是哪个账号在什么时间执行了什么。

**模型后端兼容性**：优先使用原生 function calling；若后端不支持（部分第三方中转或本地小模型会直接报错），会**自动降级**为 JSON 指令的文本协议重试，无需手动切换。若你的后端**静默忽略**工具参数（不报错、但从不调用工具），可把 `openai.agent_protocol` 显式设为 `prompt`。

> **本地模型**：本项目走标准 OpenAI 接口协议，配合 OMLX / Ollama / vLLM 等本地推理服务即可离线运行 AI 助手——`api_url` 指向服务地址、`model` 填模型名即可。建议选用支持 function calling 的模型（如 Qwen3 系列），体验最接近云端；小模型上「引导式询问」的价值更明显，因为它能替模型兜住信息不全的情况。


#### 6.6 AI 识别文件名

上表最后一项「AI 识别文件名」是 AI 在**自动识别流程**里的一个应用，不是独立的 AI 功能：
它与 AI 助手共用同一个客户端和同一份 `openai.*` 配置，**不需要另外申请 API Key**。

**它做什么**：把文件名交给模型读一遍，得到片名、年份、季集号，再拿这些字段回查 TMDB。

**什么时候触发**：它是识别回退链的第 4 层，只有前三层都失败才会走到。

| 顺序 | 手段 | 性质 |
|---|---|---|
| 1 | 识别词 + 文件名解析 | 离线正则，不联网 |
| 2 | TMDB API（必要时去掉年份重查） | 网络请求 |
| 3 | WEB 增强识别 | 网页检索，较慢 |
| 4 | **AI 识别文件名** | 调模型，默认关 |
| 5 | 关键字猜测 | 搜索引擎 |

所以 **前三层成功时它一次都不会被调用**——开着不拖慢正常流程；它的价值集中在冷门片、番剧和命名很乱的资源上。
触发点有两处：种子名识别（搜索、RSS、订阅匹配时）与文件整理（转移入库时）。

**与另外几个识别开关的区别**：「辅助识别」「WEB 增强识别」「增强识别 V2」都是离线或纯网络手段，不调用 AI；只有这一项需要 AI 服务。

**启用前提**：`OpenAI API Key` 已填写（就是上面 AI 助手那一份）。没填时该开关无效，可先用「测试连接」确认 AI 配置是通的。

**输出格式容错**：模型返回的 JSON 即使被包在 Markdown 代码围栏里、或前后带说明文字，也能正常解析；确实解析不出来时会在日志里打出原始返回，便于定位。

#### 6.7 工具调用协议的两个兼容性修复（v5.0.2）

在飞书发纯片名（如「逃出绝命街」）曾出现 AI 回「抱歉，我这次没能正确理解你的指令」，
而日志两行都在指向别处 —— 实际是**两个独立缺陷**叠加：

| # | 日志里看到什么 | 根因 |
|---|---|---|
| 1 | `当前模型不支持 function calling（… function_call: data did not match any variant of untagged enum FunctionCall …），改用文本协议` | 请求体里显式传了 `function_call="auto"`，而部分网关的 `FunctionCall` 是**不接受字符串**的 untagged enum → 400。错误文本含 `function` 被误判成「模型不支持」，于是无谓降级 |
| 2 | `模型输出疑似工具调用但未能识别，已拦下不外发：{…} </think> {…}` | 带思考的模型把工具调用**输出了两遍**、中间夹着思维链结束标签；旧解析取「首尾大括号之间」整段 `json.loads`，两个对象拼一起必然失败 |

修复后：

- **不再传 `function_call`**（OpenAI 规范中带 `functions` 时默认即为 `auto`），
  该网关不再 400，直接走原生 function calling；
- 解析前**先剥思维链**，并改用 `raw_decode()` 逐个 `{` 尝试 —— 解析出一个完整对象
  就返回，天然忽略其后的多余内容（重复输出、尾随说明都不再是问题）；
- 降级日志文案改为「原生 function calling 不可用」，不再把网关兼容问题说成模型能力问题。

> 若你的后端**确实**不支持 functions，降级到文本协议的老兜底仍然保留，行为与之前一致。

### 7. 其他

- 修复 `app/indexer/client/_base.py` 中「增强识别V2」误读配置键的问题（此前种子搜索路径上的识别增强闸门实际由「入库通知精简」开关控制，导致两个开关串线）；
- 优化用户认证与权限分级（WEB 登录 + 用户管理）；
- 优化新手刷流体验；
- 页脚仅展示版本号（不再附带提交短哈希，v3.8.0 起）。

> 历史增量说明见 [diff.md](diff.md)，版本变更见 [CHANGELOG.md](CHANGELOG.md)，各版本发布日期见 [CHANGELOG_DATES.md](CHANGELOG_DATES.md)。
