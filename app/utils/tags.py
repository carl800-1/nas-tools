"""
标签工具：统一标签的解析、归一化与合并。

标签的唯一「真源」是用户自己填的位置，程序只做读取与拼接：

1. ``config.yaml`` 的 ``pt.tags``      —— 标签库（可选，供页面下拉/补全与展示样式）
2. 站点设置里的「站点标签」            —— 按站点生效
3. 刷流任务里的「标签」                —— 按任务生效
4. 下载设置里的「标签」                —— 按下载设置生效

历史版本会在下载器中额外追加写死的中文标签（例如 ``已整理``）。自 v5.1.3 起
**程序不再向用户标签集合里追加任何默认标签**，只保留「读取」能力：如果用户
自己在上面任一位置填写了同名标签，程序依然认得它。

分隔符统一为英文逗号 ``,``。历史版本在下载设置 / 站点标签处按分号 ``;`` 拆分，
与刷流任务侧的逗号拆分不一致，导致用分号分隔的标签永远匹配不上，本模块统一修正。
"""

from config import Config

# 标签分隔符（唯一标准，英文逗号）
TAG_SEPARATOR = ","

# 兼容历史写法：分号同样视为分隔符，避免用户旧配置里的标签变成一个整体
LEGACY_SEPARATORS = (";", "；", "，", "\n", "|")

# 标签库 / 整理标记在 config.yaml 中的默认值
DEFAULT_ORGANIZED_TAG = "已整理"


class Tags:
    """
    标签工具类。所有方法均为静态方法，可直接通过 ``Tags.xxx()`` 调用。
    """

    SEPARATOR = TAG_SEPARATOR
    DEFAULT_ORGANIZED_TAG = DEFAULT_ORGANIZED_TAG

    @staticmethod
    def _get_pt_config():
        """
        安全读取 config.yaml 的 pt 段，读取失败时返回空字典。
        """
        try:
            return Config().get_config("pt") or {}
        except Exception:
            return {}

    @staticmethod
    def dedup(items):
        """
        去空白、去重、保持原有顺序。
        """
        result = []
        seen = set()
        for item in items:
            if item is None:
                continue
            name = str(item).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            result.append(name)
        return result

    @staticmethod
    def split(value):
        """
        把标签字符串拆分为标签列表，自动去空白、去重、保序。

        :param value: 字符串（"a,b;c"）、列表、元组或 None
        :return: 标签列表
        """
        if not value:
            return []
        if isinstance(value, (list, tuple, set)):
            raw_items = []
            for item in value:
                raw_items.extend(Tags.split(item))
            return Tags.dedup(raw_items)
        if not isinstance(value, str):
            value = str(value)
        # 统一历史分隔符
        normalized = value
        for sep in LEGACY_SEPARATORS:
            normalized = normalized.replace(sep, TAG_SEPARATOR)
        return Tags.dedup(normalized.split(TAG_SEPARATOR))

    @staticmethod
    def join(value):
        """
        把任意形式的标签规范化为逗号分隔的字符串，便于回写与展示。
        """
        return TAG_SEPARATOR.join(Tags.split(value))

    @staticmethod
    def get_library():
        """
        读取用户定义的标签库（config.yaml 的 pt.tags）。

        :return: 标签名列表，未配置时返回空列表
        """
        return Tags.split(Tags._get_pt_config().get("tags"))

    @staticmethod
    def save_library(tags):
        """
        保存用户定义的标签库到 config.yaml 的 pt.tags。

        必须**就地修改**配置对象：config.yaml 是用 ruamel 的注释感知结构
        加载的，一旦用 ``dict()`` 之类做浅拷贝，所有注释都会在写盘时丢失
        （实测：拷贝后配置文件里的说明注释全部消失）。因此这里直接改原对象。

        :param tags: 字符串或列表形式的标签
        :return: 保存后的标签列表
        """
        normalized = Tags.split(tags)
        config = Config()
        current = config.get_config()
        if current is None:
            current = {}
            config.save_config(current)
        if "pt" not in current or current.get("pt") is None:
            current["pt"] = {}
        current["pt"]["tags"] = TAG_SEPARATOR.join(normalized)
        config.save_config(current)
        return normalized

    @staticmethod
    def get_organized_tag():
        """
        读取「整理标记」标签名。

        程序**只读不写**：仅用于判断某任务是否已被整理过，避免重复整理。
        用户可在 config.yaml 里把它改成自己习惯的名字（例如「已转移」）。

        :return: 标签名，未配置时返回 None（表示不做整理去重保护）
        """
        configured = Tags._get_pt_config().get("tag_organized")
        if configured is None:
            return DEFAULT_ORGANIZED_TAG
        name = str(configured).strip()
        return name or None

    @staticmethod
    def is_organized(tags):
        """
        判断标签集合中是否已包含「整理标记」标签。

        :param tags: 字符串或列表形式的标签
        :return: bool
        """
        organized_tag = Tags.get_organized_tag()
        if not organized_tag:
            return False
        return organized_tag in Tags.split(tags)

    @staticmethod
    def merge(*sources):
        """
        合并多组标签，用户标签在前、程序补充在后，整体去重。

        调用方约定：**用户填写的部分作为前面的参数传入**，程序自身的兜底值放最后，
        这样展示时天然是「你的标签在前，程序补充在后」，视觉区分也据此实现。

        :param sources: 任意组标签（字符串或列表）
        :return: 去重后的标签列表
        """
        merged = []
        for source in sources:
            merged.extend(Tags.split(source))
        return Tags.dedup(merged)
