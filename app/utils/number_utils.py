class NumberUtils:

    @staticmethod
    def max_ele(a, b):
        """
        返回非空最大值
        """
        if not a:
            return b
        if not b:
            return a
        return max(int(a), int(b))

    @staticmethod
    def get_size_gb(size):
        """
        将字节转换为GB
        """
        if not size:
            return 0.0
        return float(size) / 1024 / 1024 / 1024

    @staticmethod
    def get_int(value, default=0):
        """
        安全地把配置值转成 int。

        配置文件里的值可能是字符串（如 "5000"）、空串或 None，
        直接 int() 会抛异常，这里统一兜底到 default。

        :param value: 原始值
        :param default: 转换失败时的默认值
        :return: int
        """
        try:
            if value is None or value == "":
                return default
            return int(float(value))
        except (TypeError, ValueError):
            return default
