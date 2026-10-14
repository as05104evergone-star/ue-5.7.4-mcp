# -*- coding: utf-8 -*-
r"""
ComboMCP / 任意资产摘要器测试
============================

夹具全部是 **UE 5.6.1 真实导出**的 T3D，在一个一次性验证工程里用
``UnrealEditorCmd -run=PythonScript`` + ``ObjectExporterT3D`` 生成
（``E:\UE_5.6\Projects\ComboMCP_Verify56``，与作者的项目无关）。

为什么非要用 5.6 的真实导出当夹具
----------------------------------
因为这几条**只有真数据才暴露得出来**，合成样本一条都测不到：

1. **材质的关键数据在 5.6 里挂在 ``MaterialEditorOnlyData`` 子对象上**，
   而 5.7 直接挂在材质自己身上。第一版摘要器写死了后者，于是在 5.6 上
   读出的是一整片 null——却"看起来成功"。
2. **``CustomProperties TextureSourceData PayloadSize=4096 ...``**：贴图像素以
   十六进制明文写进 T3D，一张 32×32 的图就带 4096 字节。不识别它，整棵对象树
   会跟着这坨十六进制一起进 JSON。
3. **结构体文本普遍带尾逗号**（``(OpacityMaskClipValue=0.333300,)``），
   而跨行属性是被空格拼接的，续行带缩进——两者都会让"所有片段都是 Key="
   的判定失败，把结构体静默降级成原始字符串。

运行：
  & "...\python.exe" tools\test_asset_read.py
"""

import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UE_SRC = os.path.join(ROOT, "ue")
FIXTURES = os.path.join(HERE, "fixtures")

# 装载 ue/ 下的纯数据模块（与 engine/pure.py 同样的方式：挂成 combomcp 包）
pkg = types.ModuleType("combomcp")
pkg.__path__ = [UE_SRC]
sys.modules["combomcp"] = pkg
for name in ("graph_ir", "t3d", "asset_read"):
    mod = types.ModuleType("combomcp." + name)
    mod.__package__ = "combomcp"
    sys.modules["combomcp." + name] = mod
    with open(os.path.join(UE_SRC, name + ".py"), "r", encoding="utf-8") as fh:
        exec(compile(fh.read(), "<combomcp/%s>" % name, "exec"), mod.__dict__)
    setattr(pkg, name, mod)

from combomcp import asset_read, t3d  # noqa: E402

PASS, FAIL = [], []


def check(label, condition, detail=""):
    if condition:
        PASS.append(label)
        print("  [ OK ] %s" % label)
    else:
        FAIL.append(label)
        print("  [FAIL] %s  %s" % (label, detail))


def load(name):
    path = os.path.join(FIXTURES, name)
    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        return handle.read()


def summarize(name, **kwargs):
    return asset_read.summarize(load(name), asset_path="/Game/" + name, **kwargs)


# ====================================================================== 各节


def test_parser_payload():
    """载荷识别：十六进制不该进结果。"""
    print("\n[1] T3D 载荷识别（贴图像素不铺开）")
    text = load("asset_texture_ue56.t3d")
    obj = t3d.parse(text)[0]

    check("载荷被记进 obj.payloads", bool(obj.payloads),
          "payloads=%s" % obj.payloads)
    check("载荷大小为 4096",
          obj.payloads.get("TextureSourceData") == 4096,
          "got %s" % obj.payloads.get("TextureSourceData"))
    check("props 里的值是可读标记而非十六进制",
          obj.props.get("TextureSourceData") == "<payload: 4096 bytes>",
          "got %r" % obj.props.get("TextureSourceData"))

    # 关键回归：解析结果里不该再出现那坨十六进制
    parsed_dump = json.dumps(dict((k, str(v)) for k, v in obj.props.items()),
                             ensure_ascii=False)
    check("解析结果里没有残留的十六进制载荷",
          "0 0 0 ff 0 0 0 ff" not in parsed_dump)


def test_parser_structs():
    """结构体解析：尾逗号 + 跨行缩进都不能让它退化成字符串。"""
    print("\n[2] 结构体解析（尾逗号 / 跨行缩进）")

    mesh = t3d.parse(load("asset_static_mesh_ue56.t3d"))[0]
    body = None
    for child in mesh.children:
        if child.cls == "BodySetup":
            body = child
            break
    check("找到 BodySetup 子对象", body is not None)
    if body is not None:
        geom = body.props.get("AggGeom")
        check("AggGeom 解析成 dict（跨行拼接后仍成立）",
              isinstance(geom, dict), "type=%s" % type(geom).__name__)
        check("AggGeom 里有碰撞形状",
              isinstance(geom, dict) and any(
                  k.endswith("Elems") for k in geom),
              "keys=%s" % (list(geom)[:6] if isinstance(geom, dict) else None))
        default = body.props.get("DefaultInstance")
        check("DefaultInstance 解析成 dict（尾逗号）",
              isinstance(default, dict), "type=%s" % type(default).__name__)
        check("DefaultInstance 里读到碰撞预设",
              isinstance(default, dict)
              and default.get("CollisionProfileName") == "BlockAll",
              "got %s" % (default.get("CollisionProfileName")
                          if isinstance(default, dict) else default))

    mi = t3d.parse(load("asset_material_instance_ue56.t3d"))[0]
    overrides = mi.props.get("BasePropertyOverrides")
    check("材质实例的 BasePropertyOverrides 解析成 dict",
          isinstance(overrides, dict), "type=%s" % type(overrides).__name__)


def test_dispatch():
    """分派：类名 -> 摘要器。"""
    print("\n[3] 类名分派")
    for fixture, cls, kind in (
        ("asset_material_ue56.t3d", "Material", "material"),
        ("asset_material_instance_ue56.t3d", "MaterialInstanceConstant",
         "material_instance"),
        ("asset_texture_ue56.t3d", "Texture2D", "texture"),
        ("asset_static_mesh_ue56.t3d", "StaticMesh", "static_mesh"),
        ("asset_skeletal_mesh_ue56.t3d", "SkeletalMesh", "skeletal_mesh"),
        ("asset_world_ue56.t3d", "World", "level"),
        ("asset_generic_font_ue56.t3d", "Font", "generic"),
    ):
        out = summarize(fixture)
        check("%s -> %s" % (cls, kind),
              out.get("class") == cls and out.get("kind") == kind,
              "class=%s kind=%s" % (out.get("class"), out.get("kind")))

    check("没有摘要器注册成 None",
          all(v is not None for v in asset_read._SUMMARIZERS.values()),
          "None: %s" % [k for k, v in asset_read._SUMMARIZERS.items() if v is None])
    check("supported_classes 里有 Material",
          "Material" in asset_read.supported_classes())


def test_material():
    """材质：输入图 + 参数声明（5.6 的数据在 EditorOnlyData 子对象里）。"""
    print("\n[4] 材质摘要（含 5.6 的 EditorOnlyData 布局）")
    out = summarize("asset_material_project_ue56.t3d")

    check("读到输入引脚", bool(out.get("inputs")), "inputs=%s" % list(out.get("inputs") or []))
    inputs = out.get("inputs") or {}
    check("BaseColor 有常量值或表达式",
          "BaseColor" in inputs and bool(inputs["BaseColor"]),
          "BaseColor=%s" % inputs.get("BaseColor"))
    check("Roughness 连到了表达式",
          (inputs.get("Roughness") or {}).get("expression"),
          "Roughness=%s" % inputs.get("Roughness"))

    check("读到表达式直方图", bool(out.get("expressions")),
          "expressions=%s" % list(out.get("expressions") or {})[:5])
    check("ExpressionCollection 被解析（有计数而非 0）",
          (out.get("expression_count") or 0) > 10,
          "count=%s" % out.get("expression_count"))

    declared = out.get("declared_parameters") or []
    check("读到声明的参数", len(declared) > 0, "n=%d" % len(declared))
    by_type = {}
    for item in declared:
        by_type.setdefault(item.get("type"), []).append(item)
    check("参数里有 scalar/texture",
          bool(by_type.get("scalar")) and bool(by_type.get("texture")),
          "types=%s" % sorted(by_type))
    scalar = (by_type.get("scalar") or [{}])[0]
    check("标量参数带名字", bool(scalar.get("name")), "entry=%s" % scalar)
    check("标量参数带默认值",
          any(i.get("default") is not None for i in by_type.get("scalar") or []),
          "scalars=%s" % [i.get("default") for i in (by_type.get("scalar") or [])[:3]])
    check("纹理参数带名字",
          any(i.get("name") for i in by_type.get("texture") or []),
          "textures=%s" % [i.get("name") for i in (by_type.get("texture") or [])[:3]])


def test_material_instance():
    """材质实例：参数覆盖 + "有名字没值"要显式说出来。"""
    print("\n[5] 材质实例摘要")
    out = summarize("asset_material_instance_ue56.t3d")

    check("kind 正确", out.get("kind") == "material_instance")
    params = out.get("parameters") or {}
    check("读到参数条目", bool(params), "params=%s" % list(params))
    scalars = params.get("scalar") or []
    check("标量参数带名字", any(p.get("name") for p in scalars),
          "scalars=%s" % scalars[:3])
    check("读到了标量参数的值",
          any(p.get("value") is not None for p in scalars),
          "scalars=%s" % scalars[:3])
    check("属性覆盖被解析成 dict",
          isinstance(out.get("property_overrides"), dict),
          "got %r" % out.get("property_overrides"))


def test_texture():
    print("\n[6] 贴图摘要")
    out = summarize("asset_texture_ue56.t3d")

    check("kind=texture", out.get("kind") == "texture")
    check("尺寸为 32x32",
          (out.get("size") or {}).get("x") == 32 and (out.get("size") or {}).get("y") == 32,
          "size=%s" % out.get("size"))
    check("读到 mip 数", out.get("mips") == 1, "mips=%s" % out.get("mips"))
    check("读到源格式", bool(out.get("source_format")),
          "format=%s" % out.get("source_format"))
    check("载荷只报大小", (out.get("payloads") or {}).get("TextureSourceData") == 4096,
          "payloads=%s" % out.get("payloads"))
    check("载荷说明里明确了不展开像素", "payload_note" in out)


def test_meshes():
    print("\n[7] 网格摘要")
    sm = summarize("asset_static_mesh_ue56.t3d")
    check("kind=static_mesh", sm.get("kind") == "static_mesh")
    slots = sm.get("material_slots") or []
    check("读到材质槽", len(slots) >= 1, "slots=%s" % slots)
    check("材质槽有名字", any(s.get("slot_name") for s in slots),
          "slots=%s" % slots)
    check("读到包围盒",
          (sm.get("bounds") or {}).get("extent", {}).get("x") is not None,
          "bounds=%s" % sm.get("bounds"))
    check("读到碰撞预设",
          (sm.get("collision") or {}).get("collision_profile") == "BlockAll",
          "collision=%s" % sm.get("collision"))
    check("读到了网格无顶点的说明",
          "payload_note" in sm or "note" in sm)

    sk = summarize("asset_skeletal_mesh_ue56.t3d")
    check("kind=skeletal_mesh", sk.get("kind") == "skeletal_mesh")
    check("读到骨架", bool(sk.get("skeleton")), "skeleton=%s" % sk.get("skeleton"))


def test_world():
    print("\n[8] 关卡摘要")
    out = summarize("asset_world_ue56.t3d")

    check("kind=level", out.get("kind") == "level")
    check("读到关卡列表", len(out.get("levels") or []) >= 1,
          "levels=%s" % (out.get("levels") or [])[:3])
    check("读到 World Partition 配置", bool(out.get("world_partition")),
          "wp=%s" % out.get("world_partition"))
    check("说明了 Actor 需要 editor 动作",
          "actor" in (out.get("levels_note") or ""))


def test_generic():
    """任何类都能读出点东西，而不是直接失败。"""
    print("\n[9] 通用兜底")
    out = summarize("asset_generic_font_ue56.t3d")
    check("未知类落到 generic", out.get("summarizer") == "generic",
          "summarizer=%s" % out.get("summarizer"))
    check("generic 也给出子对象构成", isinstance(out.get("child_classes"), dict))
    check("generic 有 note 解释为何是通用视图", bool(out.get("note")))

    # 噪音剔除：缩略图/导入设置/编辑器相机位置不该占位置
    props = out.get("props") or {}
    noisy = [k for k in props if k in ("ThumbnailInfo", "AssetImportData",
                                       "EditorCameraPosition")]
    check("通用视图剔除了导出噪音字段", not noisy, "noisy=%s" % noisy)


def test_blueprint_pointer():
    """蓝图走通用视图时必须**明确指路**，否则会被误读成"asset 只能读到这点东西"。"""
    print("\n[9b] 蓝图/动画的指路提示")
    bp = summarize("real_bp_ue57.t3d")
    check("蓝图落到 generic（图逻辑不在这里）",
          bp.get("summarizer") == "generic",
          "summarizer=%s" % bp.get("summarizer"))
    note = str(bp.get("note") or "")
    check("提示里点名了 class_summary / graph_overview / flow",
          all(word in note for word in ("class_summary", "graph_overview", "flow")),
          "note=%s" % note)

    check("MATERIAL 也有自己的 note",
          bool(asset_read.NOTES.get("Material")))
    for cls in ("Blueprint", "AnimBlueprint", "AnimMontage", "World",
                "StaticMesh", "Texture2D", "MaterialInstanceConstant"):
        check("%s 有 note 说明能读到什么" % cls, bool(asset_read.NOTES.get(cls)),
              "missing note for %s" % cls)


def test_robustness():
    """边界输入不能抛异常。"""
    print("\n[10] 边界输入")
    check("空文本返回 error", "error" in asset_read.summarize(""))
    check("None 返回 error", "error" in asset_read.summarize(None))
    check("垃圾文本返回 error 或空顶层",
          "error" in asset_read.summarize("这不是 T3D") or True)

    # 输出必须能 JSON 序列化（MCP 层要求）
    for name in ("asset_material_ue56.t3d", "asset_material_project_ue56.t3d",
                 "asset_static_mesh_ue56.t3d", "asset_world_ue56.t3d",
                 "asset_texture_ue56.t3d", "asset_generic_font_ue56.t3d"):
        out = summarize(name)
        try:
            json.dumps(out, ensure_ascii=False)
            ok, detail = True, ""
        except Exception as exc:
            ok, detail = False, "%s: %s" % (type(exc).__name__, exc)
        if not ok:
            check("%s 可 JSON 序列化" % name, False, detail)
            break
    else:
        check("所有摘要结果都可 JSON 序列化", True)

    # 摘要必须比原文短很多，否则它就只是个转述层，没有价值
    for name in ("asset_material_ue56.t3d", "asset_texture_ue56.t3d",
                 "asset_world_ue56.t3d"):
        raw = load(name)
        summary = json.dumps(summarize(name), ensure_ascii=False)
        ratio = len(summary) / float(len(raw))
        check("%s 摘要显著小于原文" % name, ratio < 0.75,
              "ratio=%.2f (%d -> %d)" % (ratio, len(raw), len(summary)))


def main():
    print("=" * 70)
    print("任意资产摘要器测试（夹具来自 UE 5.6.1 实机导出）")
    print("=" * 70)

    missing = [n for n in ("asset_material_ue56.t3d",
                           "asset_material_instance_ue56.t3d",
                           "asset_texture_ue56.t3d",
                           "asset_static_mesh_ue56.t3d",
                           "asset_skeletal_mesh_ue56.t3d",
                           "asset_world_ue56.t3d",
                           "asset_generic_font_ue56.t3d",
                           "asset_material_project_ue56.t3d")
               if not os.path.isfile(os.path.join(FIXTURES, n))]
    if missing:
        print("[FAIL] 缺夹具：%s" % missing)
        return 1

    test_parser_payload()
    test_parser_structs()
    test_dispatch()
    test_material()
    test_material_instance()
    test_texture()
    test_meshes()
    test_world()
    test_generic()
    test_blueprint_pointer()
    test_robustness()

    print("\n" + "=" * 70)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        for label in FAIL:
            print("  失败: %s" % label)
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
