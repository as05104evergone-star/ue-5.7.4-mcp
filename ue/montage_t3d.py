# -*- coding: utf-8 -*-
r"""
ComboMCP / Montage 的 T3D 读取与时间轴诊断（纯数据，不依赖 unreal）
=================================================================

为什么 Montage 也必须走 T3D：``UAnimMontage::CompositeSections`` 与
``SlotAnimTracks`` 在引擎里是**裸** ``UPROPERTY()``，而 Python 的
``get_editor_property`` 走的是 :cpp:func:`CanGetPropertyValue`：

    if (!InProp->HasAnyPropertyFlags(CPF_Edit | CPF_BlueprintVisible | CPF_BlueprintAssignable))
        return PermissionDenied;

裸 ``UPROPERTY()`` 三个都没有，所以**反射一定读不到段落与槽位**——而这正是
连招时序诊断的全部依据。相反，T3D 导出走的是 :cpp:func:`FProperty::ShouldPort`，
它只在 ``PPF_PropertyWindow`` 时才要求 ``CPF_Edit``，因此 T3D 里这些数据是完整的。

实测（用户项目 AS_Combo02_01）的真实 T3D 形状::

    CompositeSections(0)=(SectionName="Default",SegmentIndex=0,
        SegmentLength=2.533333,LinkedSequence="...")
    SlotAnimTracks(0)=(AnimTrack=(AnimSegments=((AnimReference="...",
        AnimEndTime=2.533333))))
    Notifies(0)=(TriggerTimeOffset=0.000100,NotifyName="ANS_InputDisabled_C",
        NotifyStateClass="...",Duration=0.507989,
        EndLink=(...LinkValue=0.507989...),Guid=...)
    SequenceLength=2.533333
    AnimNotifyTracks(0)=(TrackName="1",TrackColor=(...))

时间语义：通知/段落的绝对时间藏在 ``FAnimLinkableElement`` 的 ``LinkValue`` 上，
而 ``LinkValue`` **只在非默认值时才写出**（默认 0.0）。所以缺省即 0。
"""

import re

from . import t3d as t3d_parser


# ====================================================================== 工具


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


_RE_QUOTED = re.compile(r'"([^"]*)"')
_RE_OBJECT_TAIL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*'?\"?$")


def short_object_name(value):
    """``/Script/Engine.AnimSequence'/Game/.../AS_X.AS_X'`` -> ``AS_X``"""
    return t3d_parser._object_short_name(value)


_RE_OBJECT_PATH = re.compile(r"'([^']+)'")


def _split_object_ref(value):
    """把 T3D 的资产引用拆成 ``(对象路径, 单引号里的名字)``。

    T3D 里同一个字段有两种写法，必须都认::

        AnimReference="/Script/Engine.AnimSequence'/Game/A/AS_X.AS_X'"
        NotifyStateClass="/Game/A/ANS_X.ANS_X_C'ANS_X_C_0'"

    前者单引号里就是对象路径；后者单引号里是**实例名**，路径在前面。
    """
    if not isinstance(value, str):
        return None, None
    match = _RE_OBJECT_PATH.search(value)
    quoted = match.group(1) if match else None
    head = value.split("'", 1)[0].strip()
    return (head or None), quoted


def object_path(value):
    """``/Script/Engine.AnimSequence'/Game/.../AS_X.AS_X'`` -> ``/Game/.../AS_X``。

    两种引用写法都支持；对象路径末尾的 ``.资产名`` 会去掉，便于和缓存索引对齐
    （``unreal.load_asset`` 两种写法都吃）。
    """
    head, quoted = _split_object_ref(value)
    if quoted and quoted.startswith("/"):
        path = quoted
    elif head and head.startswith("/"):
        path = head
    else:
        return None
    if "/" in path and "." in path.rsplit("/", 1)[-1]:
        parent, tail = path.rsplit("/", 1)
        path = parent + "/" + tail.split(".", 1)[0]
    return path


_RE_INSTANCE_SUFFIX = re.compile(r"_\d+$")


def object_class_name(value):
    """``/Game/A/ANS_X.ANS_X_C'ANS_X_C_0'`` -> ``ANS_X_C``（去掉实例序号）。"""
    head, quoted = _split_object_ref(value)
    if head and head.startswith("/"):
        tail = head.rsplit("/", 1)[-1]
        if "." in tail:
            return tail.rsplit(".", 1)[-1]
        return tail
    if quoted:
        return _RE_INSTANCE_SUFFIX.sub("", quoted)
    return None


def find_asset_object(roots, cls=None):
    """在一棵 T3D 对象树里找目标资产对象（顶层通常是它）。"""
    for obj in roots:
        if cls is None or obj.cls == cls:
            return obj
    return None


def _as_vec3(raw, keys=("X", "Y", "Z")):
    """``(X=..,Y=..,Z=..)`` -> ``[x, y, z]``（保留 4 位小数）。"""
    if not isinstance(raw, dict):
        return None
    out = []
    for key in keys:
        if key not in raw:
            return None
        out.append(round(_to_float(raw.get(key)), 4))
    return out


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def notify_instance_props(obj):
    """从内联的 C++ 通知对象里取出"挂在哪、偏多少"。

    T3D 是**增量序列化**：只有非默认值才写出来。所以缺省即
    ``Attached=True`` / ``SocketName=""`` / 偏移为零 / ``Scale=1``。
    这一点很关键——一个"什么都没写"的通知正好就是会出现在角色原点的那个。

    实测形状（UE 5.6，AM_Hero_Combo）::

        Begin Object Name="AnimNotify_PlayNiagaraEffect_0" ...
           Template="/Script/Niagara.NiagaraSystem'/Game/VFX/.../FXS_NA_Slash...'"
           LocationOffset=(X=0.000000,Y=50.000000,Z=0.000000)
           RotationOffset=(Pitch=0.000000,Yaw=-180.000000,Roll=0.000000)
           SocketName="weapon_r"
        End Object
    """
    if obj is None:
        return {}
    props = {}
    template = short_object_name(obj.props.get("Template"))
    if template:
        props["template"] = template
    socket = obj.props.get("SocketName")
    if isinstance(socket, str) and socket and socket != "None":
        props["socket_name"] = socket
    offset = _as_vec3(obj.props.get("LocationOffset"))
    if offset is not None:
        props["location_offset"] = offset
    rotation = _as_vec3(obj.props.get("RotationOffset"), ("Pitch", "Yaw", "Roll"))
    if rotation is not None:
        props["rotation_offset"] = rotation
    scale = _as_vec3(obj.props.get("Scale"))
    if scale is not None:
        props["scale"] = scale
    attached = _as_bool(obj.props.get("Attached"))
    if attached is not None:
        props["attached"] = attached
    return props


def montage_time_to_sequence_time(seq_name, seg_begin, montage_time, flat_segments):
    """蒙太奇时间 -> 序列内时间。

    一个槽位段在蒙太奇上的起点是 ``StartPos``、在序列内的起点是
    ``AnimStartTime``、按 ``AnimPlayRate`` 播放，所以::

        序列内时间 = AnimStartTime + (蒙太奇时间 - StartPos) * AnimPlayRate

    实测校验（AM_Hero_Combo 第 1 段）：``StartPos=0.75``、``AnimEndTime=2.8``、
    ``AnimPlayRate=1.3`` —— ``2.8/1.3 = 2.153846``，正好等于该段
    ``CompositeSections`` 里的 ``SegmentLength``。
    """
    for seg in flat_segments or []:
        if seg.get("anim") != seq_name:
            continue
        start_pos = seg.get("start_pos", 0.0)
        if abs(start_pos - seg_begin) > 1e-4:
            continue
        rate = seg.get("play_rate", 1.0) or 1.0
        anim_start = seg.get("anim_start", 0.0)
        return anim_start + (montage_time - start_pos) * rate
    return None


# ====================================================================== 解析


def parse_montage(text):
    """T3D 文本 -> Montage 结构字典。非 Montage 返回 ``None``。"""
    roots = t3d_parser.parse(text)
    if not roots:
        return None
    montage = roots[0]
    if montage.cls != "AnimMontage":
        return None

    length = _to_float(montage.props.get("SequenceLength"), 0.0)

    # ---- 段落
    sections = []
    for raw in montage.indexed("CompositeSections"):
        if not isinstance(raw, dict):
            continue
        start = _to_float(raw.get("LinkValue"), 0.0)
        entry = {
            "name": raw.get("SectionName") or "?",
            "start": round(start, 4),
        }
        nxt = raw.get("NextSectionName")
        if nxt and nxt not in ("None", "None "):
            entry["next"] = nxt
        seg_len = raw.get("SegmentLength")
        if seg_len is not None:
            entry["segment_length"] = round(_to_float(seg_len), 4)
        entry["segment_index"] = _to_int(raw.get("SegmentIndex"), 0)
        seq_ref = raw.get("LinkedSequence")
        seq = short_object_name(seq_ref)
        if seq:
            entry["sequence"] = seq
            full = object_path(seq_ref)
            if full:
                entry["sequence_path"] = full
        sections.append(entry)
    sections.sort(key=lambda s: s["start"])

    # ---- 槽位与动画段
    slots = []
    for raw in montage.indexed("SlotAnimTracks"):
        if not isinstance(raw, dict):
            continue
        track = raw.get("AnimTrack") or {}
        segments = []
        for seg in (track.get("AnimSegments") or []) if isinstance(track, dict) else []:
            if not isinstance(seg, dict):
                continue
            anim_ref = seg.get("AnimReference")
            anim = short_object_name(anim_ref)
            item = {"anim": anim or "?"}
            full_path = object_path(anim_ref)
            if full_path:
                item["anim_path"] = full_path
            for key, out in (("StartPos", "start_pos"), ("AnimStartTime", "anim_start"),
                             ("AnimEndTime", "anim_end"), ("AnimPlayRate", "play_rate"),
                             ("LoopingCount", "loops"),
                             ("CachedPlayLength", "cached_length")):
                if key in seg:
                    item[out] = round(_to_float(seg[key]), 4)
            segments.append(item)
        slots.append({
            "slot": raw.get("SlotName") or "DefaultSlot",
            "segments": segments,
        })

    # ---- 通知（连招诊断的核心：输入禁用窗口 / 取消窗口都在这里）
    flat_segments = []
    for slot in slots:
        for seg in slot.get("segments") or []:
            flat_segments.append(seg)

    # 内联的 C++ 通知实例是 montage 的子对象（BluePrint 通知没有实体可找）
    instances = {}
    for child in (getattr(montage, "children", None) or []):
        name = getattr(child, "name", None)
        if name:
            instances.setdefault(name, child)

    notifies = []
    for raw in montage.indexed("Notifies"):
        if not isinstance(raw, dict):
            continue
        start = _to_float(raw.get("LinkValue"), 0.0)
        duration = _to_float(raw.get("Duration"), 0.0)
        end_link = raw.get("EndLink") or {}
        if isinstance(end_link, dict) and "LinkValue" in end_link:
            end = _to_float(end_link.get("LinkValue"), start + duration)
        else:
            end = start + duration

        state_obj = raw.get("NotifyStateClass")
        single_obj = raw.get("Notify")
        name = raw.get("NotifyName") or "?"
        entry = {
            "name": name,
            "cls": (object_class_name(state_obj or single_obj)
                    or short_object_name(state_obj or single_obj)),
            "kind": "state" if state_obj else ("instant" if single_obj else "unknown"),
            "start": round(start, 4),
            "duration": round(duration, 4),
        }
        if duration:
            entry["end"] = round(end, 4)
        offset = raw.get("TriggerTimeOffset")
        if offset is not None and abs(_to_float(offset)) > 1e-6:
            entry["trigger_offset"] = round(_to_float(offset), 6)

        # ---- 内联实例 + 它挂在哪一段、对应哪个序列、序列内时间是多少
        ref_name = short_object_name(single_obj or state_obj)
        if ref_name:
            entry["instance"] = ref_name
            inst = instances.get(ref_name)
            if inst is not None:
                if getattr(inst, "cls", None):
                    entry["instance_class"] = inst.cls
                props = notify_instance_props(inst)
                if props:
                    entry["props"] = props

        seg_index = raw.get("SegmentIndex")
        if seg_index is not None:
            entry["segment_index"] = _to_int(seg_index)
        seg_begin = _to_float(raw.get("SegmentBeginTime"), 0.0)
        if seg_begin:
            entry["segment_begin"] = round(seg_begin, 4)
        seq_name = short_object_name(raw.get("LinkedSequence"))
        if seq_name:
            entry["sequence"] = seq_name
            full = object_path(raw.get("LinkedSequence"))
            if full:
                entry["sequence_path"] = full
            seq_time = montage_time_to_sequence_time(
                seq_name, seg_begin, start, flat_segments)
            if seq_time is not None:
                entry["sequence_time"] = round(seq_time, 4)

        # 通知经常不写 LinkedSequence（增量序列化只写非默认值），但它一定落在
        # 某个槽位动画段上。单槽位蒙太奇里 SegmentIndex 就是段数组下标，直接回填。
        if "sequence" not in entry and len(slots) == 1:
            seg_index_raw = entry.get("segment_index")
            segments_of_slot = slots[0].get("segments") or []
            if seg_index_raw is not None and 0 <= seg_index_raw < len(segments_of_slot):
                seg = segments_of_slot[seg_index_raw]
                if seg.get("anim"):
                    entry["sequence"] = seg["anim"]
                if seg.get("anim_path"):
                    entry["sequence_path"] = seg["anim_path"]
                seq_time = montage_time_to_sequence_time(
                    seg.get("anim"), seg.get("start_pos", 0.0), start, flat_segments)
                if seq_time is not None:
                    entry["sequence_time"] = round(seq_time, 4)

        notifies.append(entry)
    notifies.sort(key=lambda n: n["start"])

    # ---- 通知轨道
    tracks = []
    for raw in montage.indexed("AnimNotifyTracks"):
        if isinstance(raw, dict) and raw.get("TrackName") is not None:
            tracks.append(raw.get("TrackName"))

    # 混合设置（这些是 EditAnywhere，反射本来也能读，这里一并取到）
    blend_in = montage.props.get("BlendIn")
    blend_out = montage.props.get("BlendOut")

    return {
        "asset": montage.name,
        "class": "AnimMontage",
        "length": round(length, 4),
        "rate_scale": _to_float(montage.props.get("RateScale"), 1.0),
        "sections": sections,
        "slots": slots,
        "notifies": notifies,
        "notify_tracks": tracks,
        "notify_count": len(notifies),
        "section_count": len(sections),
        "blend_out_trigger_time": montage.props.get("BlendOutTriggerTime"),
        "enable_auto_blend_out": montage.props.get("bEnableAutoBlendOut"),
        "skeleton": short_object_name(montage.props.get("Skeleton")),
        "source": "t3d",
    }


# ====================================================================== 诊断


_INPUT_GATE_HINTS = ("inputdisabled", "input_disabled", "disableinput",
                     "inputlock", "blockinput", "noinput")
_CANCEL_HINTS = ("cancel", "combo", "window", "buffer", "inputwindow",
                 "chainwindow", "link")
_DAMAGE_HINTS = ("damage", "hit", "attack", "trace", "collision")


def classify_notify(name, cls_name):
    blob = ("%s %s" % (name or "", cls_name or "")).lower()
    if any(h in blob for h in _INPUT_GATE_HINTS):
        return "input_gate"
    if any(h in blob for h in _CANCEL_HINTS):
        return "cancel_window"
    if any(h in blob for h in _DAMAGE_HINTS):
        return "damage"
    return "other"


def _finding(severity, category, subject, message, evidence=None, hint=None):
    out = {"severity": severity, "category": category, "subject": subject,
           "message": message}
    if evidence:
        out["evidence"] = evidence
    if hint:
        out["hint"] = hint
    return out


def analyze_montage(montage):
    """对一个已解析的 Montage 做时间轴诊断。

    判据都是"结构上可验证的事实"，不下主观结论；每条都带 evidence 与修改方向。
    """
    findings = []
    subject = montage.get("asset") or "?"
    length = montage.get("length") or 0.0
    sections = montage.get("sections") or []
    notifies = montage.get("notifies") or []

    windows = []
    for n in notifies:
        kind = classify_notify(n.get("name"), n.get("cls"))
        start = n.get("start", 0.0)
        end = n.get("end", start)
        windows.append({
            "name": n.get("name"), "class": n.get("cls"), "kind": kind,
            "start": start, "end": end, "duration": n.get("duration", 0.0),
            "is_state": n.get("kind") == "state",
        })

    gates = [w for w in windows if w["kind"] == "input_gate"]
    cancels = [w for w in windows if w["kind"] == "cancel_window"]

    # ---- 输入门覆盖率
    if gates and length > 0:
        intervals = sorted(((w["start"], max(w["end"], w["start"])) for w in gates),
                           key=lambda x: x[0])
        covered, cursor = 0.0, 0.0
        for s, e in intervals:
            if e > cursor:
                covered += min(e, length) - max(s, cursor)
                cursor = e
        ratio = max(0.0, min(1.0, covered / length))
        if ratio > 0.95:
            findings.append(_finding(
                "error", "timeline", subject,
                "输入禁用窗口覆盖整段 Montage 的 %.0f%%，没有可输入的缝隙" % (ratio * 100),
                evidence={"length": length, "coverage": round(ratio, 3), "gates": gates},
                hint="检查这些 AnimNotifyState 的时长/起止：%s"
                     % ", ".join(g["name"] or "?" for g in gates)))
        elif ratio > 0.7:
            findings.append(_finding(
                "warning", "timeline", subject,
                "输入禁用窗口占整段的 %.0f%%，可输入时间偏少" % (ratio * 100),
                evidence={"length": length, "coverage": round(ratio, 3)},
                hint="若这是大招/不可取消段则属正常；否则缩短末端窗口。"))

    # ---- 输入门与取消窗口重叠（自相矛盾）
    for g in gates:
        for c in cancels:
            overlap = min(g["end"], c["end"]) - max(g["start"], c["start"])
            if overlap > 1e-3:
                findings.append(_finding(
                    "error", "timeline", subject,
                    "输入门 '%s' 与取消窗口 '%s' 重叠 %.3fs" % (g["name"], c["name"], overlap),
                    evidence={"input_gate": g, "cancel_window": c,
                              "overlap": round(overlap, 4)},
                    hint="两者取其一，或把取消窗口挪到输入门结束之后。"))

    # ---- 尾部断档
    if length > 0 and cancels:
        last_end = max(c["end"] for c in cancels)
        tail = length - last_end
        if tail > 0.25:
            findings.append(_finding(
                "info", "timeline", subject,
                "最后一个取消窗口在 %.3fs 结束，之后还有 %.3fs 无法衔接" % (last_end, tail),
                evidence={"length": length, "last_cancel_end": round(last_end, 4),
                          "tail": round(tail, 4)},
                hint="若为收招硬直属正常；否则这段就是连招断点。"))

    # ---- 整段都禁输入且没有取消窗口（最常见的"接不上"）
    if gates and not cancels and length > 0:
        findings.append(_finding(
            "warning", "timeline", subject,
            "有输入禁用窗口但没有任何取消类通知，段落衔接只能靠播放结束",
            evidence={"gates": gates, "length": length},
            hint="连招若要在收招阶段接下一段，需要额外的取消窗口或缩短输入门。"))

    # ---- Section 结构
    names = set(s.get("name") for s in sections)
    if sections:
        for s in sections:
            nxt = s.get("next")
            if nxt and nxt not in names:
                findings.append(_finding(
                    "error", "timeline", subject,
                    "Section '%s' 的 NextSection 指向不存在的 '%s'" % (s["name"], nxt),
                    evidence={"section": s, "available": sorted(names)},
                    hint="在 Montage 编辑器里重新指定该 Section 的 NextSection。"))
            if s.get("segment_length") and length > 0:
                seg_end = s.get("start", 0.0) + s["segment_length"]
                if seg_end > length + 1e-3:
                    findings.append(_finding(
                        "warning", "timeline", subject,
                        "Section '%s' 的段尾 %.3f 超出 Montage 长度 %.3f"
                        % (s["name"], seg_end, length),
                        evidence={"section": s, "length": length},
                        hint="确认段落边界，越界的段落在编辑器里会显示异常。"))
        if len(sections) == 1 and sections[0].get("name") == "Default":
            findings.append(_finding(
                "info", "timeline", subject,
                "只有一个名为 'Default' 的 Section：无法用段落名精确定位或跳转",
                evidence={"sections": [s["name"] for s in sections]},
                hint="若连招靠 Montage_JumpToSection 分段，需要切出具名 Section。"))
    else:
        findings.append(_finding(
            "warning", "timeline", subject,
            "Montage 没有任何 Section：段落衔接完全无法用名字定位",
            evidence={"length": length},
            hint="在 Montage 编辑器里至少切一个 Section。"))

    # ---- 通知越界
    for n in notifies:
        end = n.get("end")
        if end is not None and length > 0 and end > length + 1e-3:
            findings.append(_finding(
                "error", "timeline", subject,
                "通知 '%s' 在 %.3f 结束，超出 Montage 长度 %.3f"
                % (n["name"], end, length),
                evidence={"notify": n, "length": length},
                hint="该通知的时长或位置需要收敛到 Montage 之内。"))

    if not notifies:
        findings.append(_finding(
            "info", "timeline", subject,
            "Montage 上没有任何 AnimNotify：伤害/取消/输入窗口都不由它驱动",
            evidence={"length": length},
            hint="如果连招窗口是靠蓝图计时器做的，这属正常；否则这里就是缺口。"))

    return {
        "asset": subject,
        "length": length,
        "windows": windows,
        "findings": findings,
        "finding_count": len(findings),
        "counts_by_severity": _histogram(findings),
    }


def _histogram(findings):
    hist = {}
    for f in findings:
        hist[f["severity"]] = hist.get(f["severity"], 0) + 1
    return hist


def analyze_montage_text(text):
    """T3D 文本 -> 结构 + 诊断（一步到位）。"""
    montage = parse_montage(text)
    if montage is None:
        return None
    result = dict(montage)
    result["analysis"] = analyze_montage(montage)
    return result
