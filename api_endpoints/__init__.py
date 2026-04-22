"""
原始端点定义层。

每个模块对应一个数据源（eastmoney / tonghuashun / kaipanla / other），
内部函数只做一件事：构造 URL + params + headers，返回原始 JSON dict。
不做业务解析、不做缓存、不做聚合 —— 那些在 services/ 层完成。

新加一个第三方接口的标准流程：
    1. 在对应数据源的模块里加一个 raw_xxx() 函数
    2. 在 services/xxx_service.py 消费它，做业务解析 + 缓存
    3. 在 api.py 加 @api_endpoint 方法暴露给前端
"""
