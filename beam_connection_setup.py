# -*- coding: utf-8 -*-
"""
ANSYS Workbench Mechanical IronPython 2.7 Script
=================================================
beam_connection_setup.py
"""

import math

# ================================================================
# STEP 1 : Named Selection 탐색
# ================================================================
print("=" * 55)
print("[STEP 1] Named Selections 탐색 시작")

beamRef1 = None
beamMob1 = None

# -- 방법 A : NamedSelections.Children 직접 순회 (1단계 flat) ----
try:
    ns_root = Model.NamedSelections
    for child in ns_root.Children:
        if child.Name == "Beam_Reference_1":
            beamRef1 = child
        elif child.Name == "Beam_Mobile_1":
            beamMob1 = child
    print("  [A] flat 탐색 완료  ref={} mob={}".format(beamRef1, beamMob1))
except Exception as e:
    print("  [A] flat 탐색 오류: {}".format(e))

# -- 방법 B : GetObjectsByName (못 찾은 경우만) ------------------
if beamRef1 is None or beamMob1 is None:
    try:
        all_ns = ExtAPI.DataModel.GetObjectsByName("Beam_Reference_1")
        if all_ns:
            beamRef1 = all_ns[0]
        all_ns2 = ExtAPI.DataModel.GetObjectsByName("Beam_Mobile_1")
        if all_ns2:
            beamMob1 = all_ns2[0]
        print("  [B] GetObjectsByName 탐색 완료  ref={} mob={}".format(beamRef1, beamMob1))
    except Exception as e:
        print("  [B] GetObjectsByName 오류: {}".format(e))

if beamRef1 is None:
    raise Exception("Named Selection 'Beam_Reference_1' 을 찾을 수 없습니다!")
if beamMob1 is None:
    raise Exception("Named Selection 'Beam_Mobile_1' 을 찾을 수 없습니다!")

print("  OK : {}".format(beamRef1.Name))
print("  OK : {}".format(beamMob1.Name))


# ================================================================
# STEP 2 : Connections 객체 확보
# ================================================================
print("=" * 55)
print("[STEP 2] Connections 객체 확보")

connections = None

# -- 방법 A : Model.Connections (가장 직접적) --------------------
try:
    connections = Model.Connections
    print("  [A] Model.Connections = {}".format(connections))
except Exception as e:
    print("  [A] Model.Connections 오류: {}".format(e))

# -- 방법 B : Model.Children 에서 타입명으로 탐색 ----------------
if connections is None:
    try:
        for child in Model.Children:
            type_name = child.GetType().Name
            if "Connection" in type_name:
                connections = child
                print("  [B] Children 탐색으로 발견: {}".format(type_name))
                break
    except Exception as e:
        print("  [B] Children 탐색 오류: {}".format(e))

# -- 방법 C : DataModel.GetObjectsByType ---------------------
if connections is None:
    try:
        import Ansys.ACT.Automation.Mechanical as mech
        objs = ExtAPI.DataModel.GetObjectsByType(
            Ansys.ACT.Automation.Mechanical.Connections.Connections)
        if objs:
            connections = objs[0]
            print("  [C] GetObjectsByType 발견")
    except Exception as e:
        print("  [C] GetObjectsByType 오류: {}".format(e))

if connections is None:
    raise Exception("Connections 객체를 확보할 수 없습니다!")

print("  OK : connections = {}".format(connections))


# ================================================================
# STEP 3 : Connections - Beam 생성
# ================================================================
print("=" * 55)
print("[STEP 3] Beam Connection 생성")

beam = connections.AddBeam()
print("  beam object : {}".format(beam))

beam.Material = "Structural Steel"
beam.Radius   = Quantity(0.2, "in")

beam.ReferenceLocation = beamRef1
beam.MobileLocation    = beamMob1

print("  Material  : Structural Steel")
print("  Radius    : 0.2 in")
print("  Reference : {}".format(beamRef1.Name))
print("  Mobile    : {}".format(beamMob1.Name))


# ================================================================
# STEP 4 : 두 Named Selection 간 거리 측정
#           BoundingBox 중심점 기준 (ANSYS 내부 단위 = m)
# ================================================================
print("=" * 55)
print("[STEP 4] 거리 측정")

def get_ns_centroid(ns):
    """Named Selection 지오메트리 BoundingBox 중심 평균 (m)"""
    x_sum, y_sum, z_sum = 0.0, 0.0, 0.0
    count = 0
    try:
        for ent in ns.Location.Entities:
            bb = ent.BoundingBox
            x_sum += (bb.MaxX + bb.MinX) * 0.5
            y_sum += (bb.MaxY + bb.MinY) * 0.5
            z_sum += (bb.MaxZ + bb.MinZ) * 0.5
            count += 1
    except Exception as ex:
        print("  [경고] BoundingBox 오류: {}".format(ex))
    if count == 0:
        return None
    return (x_sum / count, y_sum / count, z_sum / count)

ref_pt = get_ns_centroid(beamRef1)
mob_pt = get_ns_centroid(beamMob1)

if ref_pt is None or mob_pt is None:
    raise Exception("Named Selection 중심 좌표를 계산할 수 없습니다!")

dx = ref_pt[0] - mob_pt[0]
dy = ref_pt[1] - mob_pt[1]
dz = ref_pt[2] - mob_pt[2]
distance_m  = math.sqrt(dx*dx + dy*dy + dz*dz)
distance_in = distance_m * 39.3700787

print("  Ref 중심 : ({:.6f}, {:.6f}, {:.6f}) m".format(*ref_pt))
print("  Mob 중심 : ({:.6f}, {:.6f}, {:.6f}) m".format(*mob_pt))
print("  거리     : {:.6f} m  /  {:.6f} in".format(distance_m, distance_in))


# ================================================================
# STEP 5 : Object Generator 설정 및 Generate 실행
# ================================================================
print("=" * 55)
print("[STEP 5] Object Generator 설정")

dist_min_m = distance_m - 0.01
dist_max_m = distance_m + 0.01

# Beam 오브젝트에 Object Generator 추가
objGen = beam.AddObjectGenerator()
print("  objGen : {}".format(objGen))

# Reference / Mobile 이름 접두어
#   "Beam_Reference" → Beam_Reference_1, Beam_Reference_2 ... 자동 매칭
objGen.ReferenceNamedSelectionPrefix = "Beam_Reference"
objGen.MobileNamedSelectionPrefix    = "Beam_Mobile"

# 거리 필터
objGen.MinimumDistance = Quantity(dist_min_m, "m")
objGen.MaximumDistance = Quantity(dist_max_m, "m")

# Ignore Original 체크
objGen.IgnoreOriginal = True

print("  Reference Prefix : Beam_Reference")
print("  Mobile Prefix    : Beam_Mobile")
print("  Min Distance     : {:.6f} m".format(dist_min_m))
print("  Max Distance     : {:.6f} m".format(dist_max_m))
print("  Ignore Original  : True")

# Generate!
objGen.Generate()

print("=" * 55)
print("[완료] Object Generator 실행 완료!")
print("=" * 55)
