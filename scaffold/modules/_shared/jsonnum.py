"""让 `numeric` 列以**数字**出去，而不是字符串。

==================== 它是**共享**的，8 个模块同一份（2026-10-13）====================

位置：`modules/_shared/jsonnum.py` —— 全仓库**只有这一份**。
每一份 Dockerfile 都 `COPY modules/_shared/jsonnum.py ./`，所以容器里
`from jsonnum import NoResponseModel` 能用。

★ 为什么不做成每个模块一份副本：8 份**内容相同、各自演化**的文件，
  就是我们已经栽过三次的那个缺陷类（多份手抄的清单，没有任何检查在读它）。
  一份源文件就不需要「保持它们一致」这件事。

★ 配套一条判据（`tests/contract/test_json_numeric_types.py` §2b）：
  模块清单**从文件系统推**（`modules/*/app.py` 里凡是建 FastAPI 的），
  逐个检查：守卫挂上了、Dockerfile 拷了共享文件、compose 的构建上下文
  够得着 `modules/_shared/`。**以后新加一个模块，它自动进网。**

⚠ 直接单跑测试（不走 `run_contract_tests.sh`）时要自己带
  `PYTHONPATH=modules/_shared` —— 跑测器里已经设了。

==================== 这个文件为什么存在（实测，不是风格）====================

`geo.py` / `app.py` 里有个 `_num()`，它的 docstring 记着同一件事。但 `_num()`
是**逐个字段套**的：M6 有 20 条 GET 路由，`_num()` 只套在 005 碰过的那 3 条上。
2026-10-13 实测（一条条打，不是 grep）：

    /v1/objects/road_section        crs_central_meridian_deg, crs_zone_width_deg,
                                    end_station_km, length_m
    /v1/objects/alignment_element   azimuth_deg, center_x, center_y, end_azimuth_deg
    /v1/objects/wim_axle            esal
    /v1/objects/wim_axle/{id}       axle_dist_mm
    /v1/geometry/sections           end_station_km, length_m, roadway_width_m
    …/{id}/alignment                arc_len_m
    …/{id}/summary                  carriageway_crossfall_pct, max_superelev_pct, …
    …/{id}/widths                   earth_shoulder_width_m, median_width_m, station_km …
    …/{id}/superelevation           earth_shoulder_left_pct …
    …/{id}/stations                 station_absolute_km
    …/{id}/elements                 azimuth_deg …
    …/{id}/samples                  elevation_m, station_km …

**20 条里 10 条漏着。** 只有 `/geo`、`/geo/reverse`、`/geo/route` 是干净的 ——
因为只有它们套了 `_num()`。

===== 根因：不是 `Decimal`，是**路由的返回注解** =====

同一份 `Decimal("117.000000")`，三种写法实测：

    无返回注解                      →  {"v": 117.0}            数字 ✓
    `-> dict[str, Any]`             →  {"v": "117.000000"}     字符串 ✗
    有注解 + 先 jsonable_encoder    →  {"v": 117.0}            数字 ✓

原因：FastAPI 把**带注解的返回**当成 response_model，交给 pydantic 序列化；
而 pydantic 的 `Decimal` JSON 序列化默认**保精度**，输出字符串。不带注解时走
`jsonable_encoder`，它把 `Decimal` 映射成 `float`。

★ 所以「M6 的 20 条路由，20 条都带 `-> dict[str, Any]`」才是这个缺陷的平台级成因。
  逐字段套 `_num()` 是**在一个会漏的机制上贴膏药** —— 谁新写一条路由忘了套，
  就再漏一个，而且**没有任何东西会红**（005 之前 §13 只比字段名，不比类型，
  所以那个字符串缺陷全绿通过）。

===== 改法：**结构性**地让注解不再触发 response_model =====

把 `response_model` 强制成 `None`，注解就只剩注释作用，序列化统一走
`jsonable_encoder`。实测覆盖嵌套结构：

    现状：  {"v":"117.000000","nested":{"w":"117.000000"},"list":["117.000000"]}
    修后：  {"v":117.0,        "nested":{"w":117.0},        "list":[117.0]}

★ 一条改动覆盖**全部 20 条路由 + 以后新增的每一条**，不再依赖「记得调 `_num()`」。

===== 代价（实测，只有一条）=====

OpenAPI 里这些路由的响应 schema 从
    {"type": "object", "additionalProperties": true}
变成
    {}

两者**都不描述任何东西** —— 前者只说「是个对象，里面什么都可以」，一个字段名
都没写。所以**没有丢掉任何曾经有用的信息**。仓库里两个契约测试吃的是手写 YAML
（`contracts/openapi/*.yaml`），比的是**路径集合**，不比对响应 schema，不受影响。

⚠ `response_model=None` 也意味着**不再有响应校验/过滤**。对 `dict[str, Any]`
而言本来就没有过滤（`additionalProperties: true`），所以行为不变。

===== 用法 =====

    from jsonnum import NoResponseModel
    app = FastAPI(...)
    app.router.route_class = NoResponseModel      # ★ 注册路由**之前**

★★★★ `FastAPI(route_class=NoResponseModel)` **不行**，而且**不会报错**：
   `FastAPI.__init__` 没有 `route_class` 这个形参，多余的 kwarg 被 Starlette
   当 `**extra` 吞掉。实测：
       FastAPI(route_class=NoResponseModel)     → {"v":"117.000000"}   字符串 ✗
       app.router.route_class = NoResponseModel → {"v":117.0}          数字 ✓
   ★ 这个坑我第一次就踩了，而且**看起来像修好了** —— 是逐条打接口才抓出来的。
     所以判据（`tests/contract/test_json_numeric_types.py`）测的是**真实响应**，
     不是"源码里写了没有"。

★★ 放在自己的文件里（而不是塞进 `app.py`）是**刻意的**：`app.py` import 了
   rpdao / psycopg / 真库，测试没法离线 import 它。这个文件只依赖 fastapi，
   于是 `tests/contract/test_json_numeric_types.py` 能**把修法本身拿来跑元测试** ——
   证明「取消这个修法，判据必须变红」。
"""

from __future__ import annotations

from fastapi.routing import APIRoute

__all__ = ["NoResponseModel"]


class NoResponseModel(APIRoute):
    """强制 `response_model=None`，使返回注解不再触发 pydantic 序列化。

    ★ 为什么是「类」而不是「在每条路由上写 `response_model=None`」：
      后者要改 20 处、而且**新路由照样会漏** —— 那就还是「手写清单没人校」的老毛病。
      挂到 `route_class` 上，是对**整个 app** 生效的，新路由自动继承。
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs["response_model"] = None
        super().__init__(*args, **kwargs)
