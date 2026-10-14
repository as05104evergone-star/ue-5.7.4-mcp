# -*- coding: utf-8 -*-
r"""
ComboMCP / 通用资产语义摘要（只读、纯数据）
==========================================

把 **任意** 资产的 T3D 缓存，压成"人读得懂、模型能据此回答"的结构。

为什么需要它
------------
这个插件原先只会读蓝图与动画。但 T3D 导出对资产类型没有限制——实测在 UE 5.6 上，
材质、材质实例、贴图、静态网格、骨骼网格、关卡都能正常导出，而且内容相当完整：

* 材质带**完整的输入图**：``BaseColor=(Constant=..., Expression=".../MaterialExpressionMultiply_18")``
* 网格带材质槽、槽名、``ExtendedBounds``、碰撞几何
* 关卡带 World Partition 分区与 PersistentLevel
* 贴图带尺寸、格式、压缩设置

缺的从来不是数据，而是**把数据讲成人话的那一层**。原有的
:func:`engine.offline.generic_asset` 只做"列顶层属性 + 一个硬编码白名单"，
面对材质会吐出一串 ``BaseColor=(...)`` 原始文本，面对网格会吐出一堆
``StaticMeshDescriptionBulkData`` 子对象——信息在，但读不出来。

本模块按资产类做**有语义的摘要**，并且继承 ``ue/`` 目录的既有约定：

* **不 import unreal** —— 因此能被装进 MCP 进程离线跑（见 :mod:`engine.pure`），
  也能在编辑器里跑，两条路径共用同一份源码；
* **只读** —— 输入是字符串，输出是 dict，不碰任何资产；
* **不猜** —— 读不到的字段就不出现，并在 ``missing`` 里说明，绝不编一个默认值。
"""

import re

# ====================================================================== 工具


_MISSING = object()


def _short_ref(value):
    """``/Script/Engine.Material'/Game/A.M'`` -> ``M``；``None`` 原样返回。"""
    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"'([^']*)'", text) or re.search(r'"([^"]*)"', text)
    if match:
        text = match.group(1)
    text = text.strip().strip("'\"")
    if not text:
        return None
    # 去掉 :SubObject 尾巴，再取最后一个点之后
    tail = text.rsplit(":", 1)[-1] if ":" in text else text
    tail = tail.rsplit(".", 1)[-1]
    return tail.strip().strip("'\"") or None


def _full_ref(value):
    """取出对象引用里的**完整包路径**（``/Game/A/B.B``），不是短名。

    "这个材质父类是谁、引用了哪张贴图"这类问题要的是完整路径——短名会在
    多个同名资产之间产生歧义。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.search(r"'([^']*)'", text) or re.search(r'"([^"]*)"', text)
    if match:
        text = match.group(1)
    text = text.strip().strip("'\"")
    if not text or text.startswith("/Script/"):
        return text or None
    return text or None


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    return str(value).strip().lower() in ("true", "1")


def _as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    if value is None:
        return None
    try:
        return round(float(str(value).strip()), 6)
    except (TypeError, ValueError):
        return None


def _as_int(value):
    number = _as_number(value)
    return None if number is None else int(number)


def _trim(value, limit=200):
    """把任意值压成有界的可读文本。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        out = {}
        for key in list(value.keys())[:12]:
            out[key] = _trim(value[key], limit)
        return out
    if isinstance(value, (list, tuple)):
        return [_trim(item, limit) for item in value[:6]]
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


def _dig(node, path, default=None):
    """按 ``a.b.c`` 从嵌套 dict 里取值。"""
    current = node
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


# 材质输入名（UE 的 FMaterialInput 序列化结果）。用来把"哪些引脚真的接了东西"
# 一眼讲清楚——这是读材质时唯一值得先看的信息。
MATERIAL_INPUTS = (
    "BaseColor", "Metallic", "Specular", "Roughness", "Anisotropy", "Normal",
    "Tangent", "EmissiveColor", "Opacity", "OpacityMask", "WorldPositionOffset",
    "SubsurfaceColor", "ClearCoat", "ClearCoatRoughness", "AmbientOcclusion",
    "Refraction", "PixelDepthOffset", "Displacement", "SurfaceThickness",
)

# 参数表达式类名 -> 参数种类
_PARAM_EXPR = {
    "MaterialExpressionScalarParameter": "scalar",
    "MaterialExpressionVectorParameter": "vector",
    "MaterialExpressionTextureSampleParameter2D": "texture",
    "MaterialExpressionTextureSampleParameterCube": "texture",
    "MaterialExpressionTextureObjectParameter": "texture_object",
    "MaterialExpressionStaticBoolParameter": "static_bool",
    "MaterialExpressionStaticSwitchParameter": "static_switch",
    "MaterialExpressionFontSampleParameter": "font",
    "MaterialExpressionRuntimeVirtualTextureSampleParameter": "rvt",
}

# 表示"这个输入没接东西"的占位值
_EMPTY_REFS = ("none", "null", "")

# 导出噪音：这些字段对"这个资产是干什么的"没有任何帮助，只会占上下文。
_NOISE_KEYS = {
    "ThumbnailInfo", "AssetImportData", "EditorCameraPosition", "EditorHash",
    "LightingGuid", "ParameterStateId", "TextureStreamingDataVersion",
    "bCustomizedCollision", "ExtendedBounds", "bUseLegacyGamma",
    "Text",
}

_PAYLOAD_MARKER = re.compile(r"^<payload:\s*(\d+)\s*bytes>$")


def _split_payloads(obj):
    """把 props 分成（可读属性, 载荷）。

    载荷在解析阶段已经被换成 ``<payload: N bytes>`` 标记（见 ``ue/t3d.py``），
    这里再按标记收回 ``payloads``，让"这个资产带 N 字节二进制"这件事显式可见，
    而不是伪装成一个普通属性。
    """
    readable, payloads = {}, {}
    for key, value in obj.props.items():
        if isinstance(value, str):
            match = _PAYLOAD_MARKER.match(value)
            if match:
                payloads[key] = int(match.group(1))
                continue
        readable[key] = value
    for key, size in (getattr(obj, "payloads", None) or {}).items():
        payloads.setdefault(key, size)
    return readable, payloads


def _walk(obj, max_depth=4, _depth=0):
    """深度优先遍历对象树（含自身），产出 ``(depth, obj)``。"""
    yield _depth, obj
    if _depth >= max_depth:
        return
    for child in obj.children:
        for item in _walk(child, max_depth, _depth + 1):
            yield item


def _class_histogram(obj, max_depth=4):
    """子对象的类名直方图 —— 回答"这个资产内部由什么组成"。"""
    hist = {}
    for depth, item in _walk(obj, max_depth):
        if depth == 0:
            continue
        hist[item.cls] = hist.get(item.cls, 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: -kv[1]))


# ====================================================================== 材质


def _expr_kind(ref):
    """``/Script/Engine.MaterialExpressionMultiply'...'`` -> ``MaterialExpressionMultiply``"""
    return _short_ref(ref)


def _material_view(obj):
    """把材质的"有效属性 + 表达式对象"汇总到一处。

    **为什么必须这样**：材质的关键数据在不同引擎版本里放的位置不一样——
    实测 UE 5.6 把输入引脚与 ``ExpressionCollection`` 放在
    ``MaterialEditorOnlyData`` 子对象上，而 5.7 直接放在材质自己身上。
    写死任何一边，另一边就会读出一片 null（第一版就是这么错的）。

    返回 ``(merged_props, expression_objects, editor_only_obj)``。
    """
    merged, editor_only = _merged_view(obj)
    expressions = [c for c in obj.children
                   if c.cls.startswith("MaterialExpression")]
    # 部分版本没有表达式子对象，只有 ExpressionCollection 里的引用；
    # 那种情况下表达式清单由调用方从引用里重建。
    return merged, expressions, editor_only


def _merged_view(obj):
    """把 ``*EditorOnlyData`` 子对象上的属性并到顶层。

    不是材质独有的问题：UE 把"只在编辑器里需要"的字段统一塞进
    ``<X>EditorOnlyData`` 子对象——贴图的 ``CompressionSettings``/``SRGB``/
    ``LODGroup`` 在 5.6 里就在这里；材质的输入引脚与 ``ExpressionCollection``
    也是。不合并就会出现"同一个资产在 5.7 读得出、5.6 读不出"的假故障。

    返回 ``(merged_props, editor_only_obj)``。索引属性同样会被并入（键名不变）。
    """
    merged = dict(obj.props)
    editor_only = None
    for child in obj.children:
        if not child.cls.endswith("EditorOnlyData"):
            continue
        editor_only = child
        for key, value in child.props.items():
            merged.setdefault(key, value)
        for key, bucket in (child.indexed_props or {}).items():
            merged.setdefault(key, [bucket[i] for i in sorted(bucket)])
        break
    return merged, editor_only


def _summarize_material_function(obj, text):
    """``UMaterialFunction``：描述 + 输入 + 表达式构成。

    材质函数是"被别的材质调用的一段图"。这里给的是它的接口（有哪些输入）
    与内部规模——足以判断"这个函数改了什么、会被谁影响"。
    """
    merged, editor_only = _merged_view(obj)
    _readable, payloads = _split_payloads(obj)
    expressions = [c for c in obj.children
                   if c.cls.startswith("MaterialExpression")]
    hist = _expression_histogram(expressions, _expression_refs(merged))

    inputs = []
    raw_inputs = merged.get("FunctionInputs")
    if isinstance(raw_inputs, list):
        for item in raw_inputs:
            if not isinstance(item, dict):
                continue
            name = item.get("InputName") or item.get("Input")
            if name:
                entry = {"name": str(name)}
                if item.get("Description"):
                    entry["description"] = _trim(item.get("Description"), 80)
                if item.get("SortPriority") is not None:
                    entry["sort_priority"] = _as_int(item.get("SortPriority"))
                inputs.append(entry)

    out = {
        "kind": "material_function",
        "description": _trim(merged.get("Description"), 200),
        "inputs": inputs,
        "expression_count": sum(hist.values()),
        "expressions": dict(sorted(hist.items(), key=lambda kv: -kv[1])),
        "child_classes": _class_histogram(obj),
    }
    if editor_only is not None:
        out["editor_only_data"] = editor_only.name
    if payloads:
        out["payloads"] = payloads
    return {k: v for k, v in out.items() if v is not None}


def _expression_refs(merged):
    """从 ``ExpressionCollection`` / ``Expressions`` 里取出表达式引用列表。"""
    for key in ("ExpressionCollection", "Expressions"):
        raw = merged.get(key)
        if isinstance(raw, dict):
            refs = raw.get("Expressions")
            if isinstance(refs, list):
                return refs
        elif isinstance(raw, list):
            return raw
    return []


def _material_inputs(merged):
    """材质输入引脚：常量值 / 连的是哪个表达式。

    两种形态都要认：
      * 5.7 —— 平铺在 props 上：``BaseColor=(Constant=...,Expression=...)``
      * 5.6 —— 在一个 dict 里：``BaseColor={...}``（上面的 merge 已经处理）
    """
    out = {}
    for name in MATERIAL_INPUTS:
        raw = merged.get(name)
        if not isinstance(raw, dict):
            continue
        entry = {}
        constant = raw.get("Constant")
        if constant is not None:
            if isinstance(constant, dict) and not constant:
                pass
            elif str(constant).lower() not in _EMPTY_REFS:
                entry["constant"] = _trim(constant)
        if _as_bool(raw.get("UseConstant")) is True:
            entry["use_constant"] = True
        expr = _expr_kind(raw.get("Expression"))
        if expr and expr.lower() not in _EMPTY_REFS:
            entry["expression"] = expr
        if raw.get("OutputIndex") is not None:
            entry["output_index"] = _as_int(raw.get("OutputIndex"))
        if raw.get("Mask") is not None:
            entry["mask"] = _as_int(raw.get("Mask"))
        out[name] = entry if entry else {"connected": False}
    return out


def _expression_histogram(expressions, refs):
    """表达式类名直方图。有子对象就用子对象，否则从引用里数。"""
    hist = {}
    if expressions:
        for item in expressions:
            hist[item.cls] = hist.get(item.cls, 0) + 1
        return hist
    for ref in refs:
        kind = _expr_kind(ref)
        if kind:
            hist[kind] = hist.get(kind, 0) + 1
    return hist


def _parameter_declarations(expressions):
    """材质里**声明**了哪些参数——名字、类型、默认值。

    这是读材质时最实用的一节：材质实例能覆盖的就是这些参数。
    """
    declared = []
    for item in expressions:
        kind = _PARAM_EXPR.get(item.cls)
        if not kind:
            continue
        props = item.props
        name = props.get("ParameterName")
        if not name:
            continue
        entry = {"name": str(name), "type": kind}
        if props.get("Group") not in (None, "", "None"):
            entry["group"] = str(props.get("Group"))
        if props.get("DefaultValue") is not None:
            entry["default"] = _trim(props.get("DefaultValue"), 80)
        tex = props.get("Texture")
        if tex and str(tex).lower() not in _EMPTY_REFS:
            entry["texture"] = _full_ref(tex)
        declared.append(entry)
    declared.sort(key=lambda e: (e["type"], e["name"].lower()))
    return declared


def summarize_material(obj, text):
    """``UMaterial``：混合/着色模型 + 输入图 + 参数声明 + 表达式构成。"""
    merged, expressions, _ = _material_view(obj)
    _readable, payloads = _split_payloads(obj)
    refs = _expression_refs(merged)
    hist = _expression_histogram(expressions, refs)

    out = {
        "kind": "material",
        "blend_mode": _short_ref(merged.get("BlendMode")),
        "shading_model": _short_ref(merged.get("ShadingModel")),
        "two_sided": _as_bool(merged.get("TwoSided")),
        "material_domain": _short_ref(merged.get("MaterialDomain")),
        "inputs": _material_inputs(merged),
        "expression_count": sum(hist.values()),
        "expressions": dict(sorted(hist.items(), key=lambda kv: -kv[1])),
        "child_classes": _class_histogram(obj),
    }

    declared = _parameter_declarations(expressions)
    if declared:
        out["declared_parameters"] = declared
        out["declared_parameters_note"] = (
            "这些是材质里声明的参数；材质实例能覆盖的就是它们。")

    if merged.get("PhysMaterial") not in (None, "", "None"):
        out["phys_material"] = _full_ref(merged.get("PhysMaterial"))
    # 参数分组（编辑器里参数面板的分区）
    groups = merged.get("ParameterGroupData")
    if isinstance(groups, list):
        names = [_dig(g, "GroupName") for g in groups if isinstance(g, dict)]
        names = [str(n) for n in names if n]
        if names:
            out["parameter_groups"] = names
    if payloads:
        out["payloads"] = payloads
    return {k: v for k, v in out.items() if v is not None}


def _param_entry(raw):
    """材质/材质实例的参数项。``ParameterInfo`` 里是名字，值在同级。"""
    if not isinstance(raw, dict):
        return _trim(raw, 80)
    info = raw.get("ParameterInfo") if isinstance(raw.get("ParameterInfo"), dict) else {}
    entry = {}
    name = info.get("Name") or raw.get("Name")
    if name:
        entry["name"] = str(name)
    value = _as_number(raw.get("ParameterValue"))
    if value is not None:
        entry["value"] = value
    if "Vector" in raw:
        entry["value"] = _trim(raw.get("Vector"))
    if "Texture" in raw:
        entry["texture"] = _full_ref(raw.get("Texture"))
    if raw.get("ExpressionGUID"):
        entry["has_override"] = True
    return entry or _trim(raw, 80)


def summarize_material_instance(obj, text):
    """``UMaterialInstanceConstant``：父材质 + 参数覆盖。

    这是做材质实例时最常问的一组问题——"它继承谁""改了哪几个参数"。
    关于"有名字没值"：部分版本把参数值只写在 ``EditorOnlyData`` 子对象里，
    导出没带上，于是顶层只剩 ``ParameterInfo``（名字）。这种情况显式列进
    ``unset_parameters`` 并给出下一步，而不是让调用方以为值就是空。
    """
    merged, _expressions, _eod = _material_view(obj)
    _readable, payloads = _split_payloads(obj)

    parent = None
    for key in ("Parent", "ParentMaterial"):
        if merged.get(key) not in (None, "", "None"):
            parent = _full_ref(merged.get(key))
            break

    out = {
        "kind": "material_instance",
        "parent": parent,
        "child_classes": _class_histogram(obj),
    }

    params = {}
    unset = []
    for bucket, label in (("ScalarParameterValues", "scalar"),
                          ("VectorParameterValues", "vector"),
                          ("TextureParameterValues", "texture")):
        values = _indexed_or_list(obj, merged, bucket)
        for raw in values:
            entry = _param_entry(raw)
            if isinstance(entry, dict) and entry.get("name") and \
                    "value" not in entry and "texture" not in entry:
                unset.append("%s:%s" % (label, entry["name"]))
            params.setdefault(label, []).append(entry)
    if params:
        out["parameters"] = params
    if unset:
        out["unset_parameters"] = unset
        out["unset_note"] = ("这些参数只有名字没有值：值存在 EditorOnlyData 子对象里，"
                             "当前导出没带上。要确认实际取值请读它继承的父材质"
                             "（其中 declared_parameters 有参数默认值），"
                             "或在编辑器里直接看实例。")

    overrides = merged.get("BasePropertyOverrides")
    if isinstance(overrides, dict) and overrides:
        out["property_overrides"] = _trim(overrides)
    if payloads:
        out["payloads"] = payloads
    return {k: v for k, v in out.items() if v is not None}


def _indexed_or_list(obj, merged, key):
    """取 ``Key(0)=(...)`` 形式的数组：先看索引属性，再看普通 list。"""
    values = obj.indexed(key)
    if values:
        return values
    raw = merged.get(key)
    return raw if isinstance(raw, list) else []


# ====================================================================== 贴图


def summarize_texture(obj, text):
    """``UTexture2D``：尺寸、格式、压缩设置 —— 排查"为什么这张图糊/花"用。

    注意压缩设置/SRGB/LODGroup 在 5.6 里挂在 ``TextureEditorOnlyData`` 子对象上，
    所以必须走 :func:`_merged_view`。
    """
    readable, editor_only = _merged_view(obj)
    _r, payloads = _split_payloads(obj)
    source = readable.get("Source") if isinstance(readable.get("Source"), dict) else {}

    size = None
    imported = readable.get("ImportedSize")
    if isinstance(imported, dict):
        size = {"x": _as_int(imported.get("X")), "y": _as_int(imported.get("Y"))}
    elif source and source.get("SizeX") is not None:
        size = {"x": _as_int(source.get("SizeX")), "y": _as_int(source.get("SizeY"))}

    out = {
        "kind": "texture",
        "size": size,
        "mips": _as_int(source.get("NumMips")) if source else None,
        "slices": _as_int(source.get("NumSlices")) if source else None,
        "source_format": _short_ref(source.get("Format")) if source else None,
        "compression": _short_ref(readable.get("CompressionSettings")),
        "lod_group": _short_ref(readable.get("LODGroup")),
        "srgb": _as_bool(readable.get("SRGB")),
        "mip_gen_settings": _short_ref(readable.get("MipGenSettings")),
        "never_stream": _as_bool(readable.get("NeverStream")),
        "virtual_texture": _as_bool(readable.get("VirtualTextureStreaming")),
        "child_classes": _class_histogram(obj, max_depth=2),
    }
    if editor_only is not None:
        out["editor_only_data"] = editor_only.name
    stream = readable.get("TextureStreamingData")
    if isinstance(stream, list):
        out["streaming_entries"] = len(stream)
    if payloads:
        out["payloads"] = payloads
        out["payload_note"] = ("贴图像素以十六进制明文写在 T3D 里；这里只报大小——"
                               "要判断图像内容请直接在编辑器里打开。")
    return {k: v for k, v in out.items() if v is not None}


# ====================================================================== 网格


def summarize_static_mesh(obj, text):
    """``UStaticMesh``：材质槽 + 包围盒 + 碰撞摘要。

    网格的顶点数据是 BulkData，T3D 里没有——所以这里不去假装能读几何，
    只给出"几个材质槽、多大、有没有碰撞"这些 T3D 里确实有的东西。
    """
    readable, _editor_only = _merged_view(obj)
    _r, payloads = _split_payloads(obj)

    slots = []
    for raw in obj.indexed("StaticMaterials"):
        if not isinstance(raw, dict):
            continue
        material = _full_ref(raw.get("MaterialInterface"))
        if not material:
            material = _full_ref(raw.get("Material"))
        slots.append({
            "index": len(slots),
            "slot_name": _trim(raw.get("MaterialSlotName"), 80),
            "material": material,
        })

    bounds = readable.get("ExtendedBounds")
    box = None
    if isinstance(bounds, dict):
        extent = bounds.get("BoxExtent") if isinstance(bounds.get("BoxExtent"), dict) else {}
        box = {
            "extent": {"x": _as_number(extent.get("X")), "y": _as_number(extent.get("Y")),
                       "z": _as_number(extent.get("Z"))},
            "sphere_radius": _as_number(bounds.get("SphereRadius")),
        }

    collision = None
    for depth, item in _walk(obj, max_depth=3):
        if item.cls != "BodySetup":
            continue
        geom = item.props.get("AggGeom")
        collision = {"child_classes": _class_histogram(item, max_depth=2)}
        if isinstance(geom, dict):
            collision["shapes"] = dict(
                (k, len(v) if isinstance(v, list) else 1)
                for k, v in geom.items() if isinstance(v, (list, dict)))
        default = item.props.get("DefaultInstance")
        if isinstance(default, dict):
            collision["collision_profile"] = _trim(default.get("CollisionProfileName"), 60)
            collision["object_type"] = _short_ref(default.get("ObjectType"))
        break

    out = {
        "kind": "static_mesh",
        "material_slots": slots,
        "bounds": box,
        "collision": collision,
        "lightmap_resolution": _as_int(readable.get("LightMapResolution")),
        "lightmap_uv_index": _as_int(readable.get("LightMapCoordinateIndex")),
        "nanite": _nanite_state(obj),
        "child_classes": _class_histogram(obj),
    }
    if payloads:
        out["payloads"] = payloads
        out["payload_note"] = "网格几何存在 BulkData 里，T3D 不含顶点数据。"
    return {k: v for k, v in out.items() if v is not None}


def _nanite_state(obj):
    """``NaniteSettings`` 是个结构体；只把 enabled 抽出来，其余不铺。"""
    for depth, item in _walk(obj, max_depth=2):
        settings = item.props.get("NaniteSettings")
        if isinstance(settings, dict):
            return _as_bool(settings.get("bEnabled"))
    return None


def summarize_skeletal_mesh(obj, text):
    """``USkeletalMesh``：骨架 + 材质槽。

    材质槽的字段名在版本间变过（``StaticMaterials[].MaterialInterface`` vs
    ``Materials[].Material``），所以两种都试——宁可多一次取值，也不要静默给出
    空槽位列表（那会让人误判"这个网格没有材质"）。
    """
    readable, _editor_only = _merged_view(obj)
    _r, payloads = _split_payloads(obj)

    slots = []
    for bucket in ("StaticMaterials", "Materials"):
        for raw in obj.indexed(bucket):
            if not isinstance(raw, dict):
                continue
            material = _full_ref(raw.get("MaterialInterface"))
            if not material:
                material = _full_ref(raw.get("Material"))
            slot_name = _trim(raw.get("MaterialSlotName") or raw.get("SlotName"), 80)
            if material is None and slot_name is None:
                continue
            entry = {"index": len(slots), "material": material}
            if slot_name:
                entry["slot_name"] = slot_name
            slots.append(entry)
        if slots:
            break

    out = {
        "kind": "skeletal_mesh",
        "skeleton": _full_ref(readable.get("Skeleton")),
        "physics_asset": _full_ref(readable.get("PhysicsAsset")),
        "material_slots": slots,
        "nanite": _nanite_state(obj),
        "child_classes": _class_histogram(obj),
    }
    if payloads:
        out["payloads"] = payloads
        out["payload_note"] = "骨骼与几何存在 BulkData 里；骨架与材质槽是有的。"
    return {k: v for k, v in out.items() if v is not None}


# ====================================================================== 关卡


def summarize_world(obj, text):
    """``UWorld``：这个关卡由什么组成、是否 World Partition。

    关卡 T3D 里没有 Actor 的逐个序列化（那在 Level 子对象与 .umap 的二进制里），
    所以这里给的是**结构**信息：有哪些 Level、WP 分区怎么配、HLOD 挂了几层。
    """
    readable, payloads = _split_payloads(obj)

    levels = []
    for depth, item in _walk(obj, max_depth=3):
        if item.cls not in ("Level", "PersistentLevel"):
            continue
        levels.append({
            "name": item.name,
            "path": item.path or None,
            "child_classes": _class_histogram(item, max_depth=2),
        })
        if len(levels) >= 12:
            break

    # WorldPartition / WorldSettings 都挂在子对象上，而且比两层更深：
    #   World -> Level(PersistentLevel) -> WorldSettings -> WorldPartition
    # 第一版只走两层、又去顶层 props 里找，于是 world_partition 恒为 None。
    wp = None
    settings_view = {}
    for _depth, item in _walk(obj, max_depth=6):
        if item.cls == "WorldPartition":
            wp = wp or {}
            for key, target in (("RuntimeHash", "runtime_hash"),
                                ("EditorHash", "editor_hash"),
                                ("DefaultHLODLayer", "default_hlod_layer")):
                value = item.props.get(key)
                if value in (None, "", "None"):
                    continue
                wp[target] = (_full_ref(value) if key == "DefaultHLODLayer"
                              else _short_ref(value))
            parts = item.indexed("RuntimePartitions")
            if parts:
                wp["runtime_partitions"] = [_trim(p, 140) for p in parts[:8]]
        elif item.cls == "WorldSettings":
            for key in ("bForceNoPrecomputedLighting",
                        "bIncludeGridSizeInNameForFoliageActors",
                        "bIncludeGridSizeInNameForPartitionedActors"):
                if item.props.get(key) is not None:
                    settings_view[key] = _as_bool(item.props.get(key))
            if item.props.get("WorldPartition") not in (None, "", "None"):
                wp = wp or {}
                wp["enabled"] = True
            nav = item.props.get("NavigationSystemConfig")
            if nav not in (None, "", "None"):
                settings_view["navigation_system"] = _short_ref(nav)
            lightmass = item.props.get("LightmassSettings")
            if isinstance(lightmass, dict) and lightmass:
                settings_view["lightmass"] = _trim(lightmass)

    # Level 自身的标记：bIsPartitioned 直接说明这是不是一个 WP 关卡
    for _depth, item in _walk(obj, max_depth=4):
        if item.cls != "Level":
            continue
        if item.props.get("bIsPartitioned") is not None:
            settings_view["is_partitioned"] = _as_bool(item.props.get("bIsPartitioned"))
        if item.props.get("bUseExternalActors") is not None:
            settings_view["use_external_actors"] = _as_bool(
                item.props.get("bUseExternalActors"))
        break

    out = {
        "kind": "level",
        "levels": levels,
        "world_partition": wp,
        "world_settings": settings_view or None,
        "child_classes": _class_histogram(obj),
    }
    editor_views = obj.indexed_count("EditorViews")
    if editor_views:
        out["editor_views"] = editor_views
    if payloads:
        out["payloads"] = payloads
    out["levels_note"] = ("关卡里 Actor 的逐个序列化不在这份 T3D 里。"
                          "要列 Actor 请用 editor action=actor（需要编辑器在运行）。")
    return {k: v for k, v in out.items() if v is not None}


# ====================================================================== 通用兜底


def summarize_generic(obj, text, max_props=60):
    """没有专用摘要器时的兜底。

    比原先的 ``generic_asset`` 好在三点：

    1. **剔除导出噪音**（缩略图、导入设置、编辑器相机位置……），它们对
       "这是什么资产"毫无帮助，却稳定占掉一大半输出；
    2. **大值收敛**：长数组报 ``len=N`` 加前几项样本，不再整坨铺开；
    3. **结构可见**：给出子对象类名直方图与索引属性规模——这两样最能说明
       "这个资产内部由什么组成"。
    """
    readable, payloads = _split_payloads(obj)

    props = {}
    for key in sorted(readable.keys()):
        if key in _NOISE_KEYS or key.startswith("bIsEditor"):
            continue
        value = readable[key]
        if isinstance(value, list):
            props[key] = {"len": len(value), "sample": _trim(value[:3], 120)}
        else:
            props[key] = _trim(value, 200)
        if len(props) >= max_props:
            break

    indexed = dict((key, len(bucket))
                   for key, bucket in (obj.indexed_props or {}).items())

    out = {
        "kind": "generic",
        "props": props,
        "indexed_props": indexed or None,
        "child_classes": _class_histogram(obj),
        "children": [c.name for c in obj.children[:20]],
    }
    if payloads:
        out["payloads"] = payloads
    return {k: v for k, v in out.items() if v is not None}


# ====================================================================== 入口


# 类名 -> 摘要器。键是 T3D 里的短类名（``_short_class`` 的结果）。
_SUMMARIZERS = {
    "Material": summarize_material,
    "MaterialInstanceConstant": summarize_material_instance,
    "MaterialInstanceDynamic": summarize_material_instance,
    "MaterialFunction": _summarize_material_function,
    "Texture2D": summarize_texture,
    "TextureCube": summarize_texture,
    "TextureRenderTarget2D": summarize_texture,
    "StaticMesh": summarize_static_mesh,
    "SkeletalMesh": summarize_skeletal_mesh,
    "World": summarize_world,
}

# 这些类名值得额外说明一句"能读到什么/读不到什么"
NOTES = {
    "Material": "T3D 带完整输入图：每个输入是常量还是连着表达式，一眼可见。",
    "MaterialInstanceConstant": "参数值可能只在 EditorOnlyData 里；见 unset_parameters。",
    "Texture2D": "像素数据不展开，只报大小。",
    "StaticMesh": "几何在 BulkData 里，T3D 不含顶点；材质槽与碰撞是有的。",
    "SkeletalMesh": "几何在 BulkData 里；骨架与材质槽是有的。",
    "World": "只给结构；列 Actor 需要编辑器的 actor 动作。",
    # 蓝图/动画有**专用**读取器，通用视图读不出图逻辑——别让人以为
    # "asset 读蓝图只能读到这点东西"。
    "Blueprint": ("通用视图不含图逻辑（节点、连线、执行流）。读蓝图请用专用工具："
                  "class_summary（类里有什么）→ graph_overview（有哪些图）→ "
                  "flow（执行逻辑）；动画蓝图用 anim_asset。"),
    "AnimBlueprint": "动画蓝图请用 anim_asset（状态机 / 状态 / 转换）。",
    "AnimMontage": "蒙太奇请用 montage 工具（Section / Notify 时间轴）。",
}


def summarize(text, asset_path=None, max_props=60):
    """把一段 T3D 文本摘要成 dict。

    这是本模块唯一的入口。类名命中专用摘要器就用它，否则退到
    :func:`summarize_generic`——**任何**资产都能读出点东西，不会因为
    "这个类没有专用实现"而直接失败。

    ``text`` 为 None/空时返回带 ``error`` 的 dict（调用方据此提示先 sync）。
    """
    if not text:
        return {"error": "no T3D text given"}

    # 延迟取模块，避免顶层循环 import：ue/t3d.py 与本模块在同一个包下互相独立。
    from . import t3d as t3d_mod

    roots = t3d_mod.parse(text)
    if not roots:
        return {"error": "T3D 解析后没有任何顶层对象"}

    obj = roots[0]
    summarizer = _SUMMARIZERS.get(obj.cls)
    # 类专属说明优先：它比"没有专用摘要器"这句更有用（例如蓝图那句会点名
    # class_summary / graph_overview / flow）。通用那句只在没有专属说明时补上。
    told = NOTES.get(obj.cls)
    if summarizer is not None:
        used = obj.cls
        payload = summarizer(obj, text)
    else:
        used = "generic"
        payload = summarize_generic(obj, text, max_props=max_props)
        if not told:
            told = ("类 %s 没有专用摘要器，已退到通用视图（属性 + 子对象构成）。"
                    % obj.cls)

    result = {
        "asset_path": asset_path,
        "asset": obj.name,
        "class": obj.cls,
        "summarizer": used,
        "child_count": len(obj.children),
        "source": "t3d-cache",
    }
    result.update(payload)
    if told:
        result["note"] = told
    return result


def supported_classes():
    """列出有专用摘要器的类（供 help/工具文档自描述）。"""
    return sorted(_SUMMARIZERS.keys())
