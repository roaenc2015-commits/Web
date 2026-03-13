# -*- coding: utf-8 -*-
"""
ANSYS Workbench Mechanical IronPython 2.7 Script
=================================================
기능:
  1. Named Selection (Beam_Reference_1, Beam_Mobile_1) 을 이용한
     Connections - Beam 연결 1개 생성
  2. 두 Named Selection 간 거리 측정
  3. Automation - Object Generator 설정 및 실행
       Reference prefix  : Beam_Reference
       Mobile prefix     : Beam_Mobile
       Distance min/max  : 측정값 ± 0.01 m
       Ignore Original   : True (체크)

사용법:
  ANSYS Mechanical -> Automation -> Script Editor 에서 실행
"""

import math

# ══════════════════════════════════════════════════════════════════
# STEP 1 : Named Selection 탐색
# ══════════════════════════════════════════════════════════════════
model = Model

beamRef1 = None
beamMob1 = None

def find_named_selection(parent, name):
    """재귀적으로 Named Selection 트리를 탐색합니다."""
    for child in parent.Children:
        if child.Name == name:
            return child
        # 하위 폴더가 있는 경우 재귀 탐색
        result = find_named_selection(child, name)
        if result is not None:
            return result
    return None

beamRef1 = find_named_selection(model.NamedSelections, "Beam_Reference_1")
beamMob1 = find_named_selection(model.NamedSelections, "Beam_Mobile_1")

if beamRef1 is None:
    raise Exception("[오류] Named Selection 'Beam_Reference_1' 을 찾을 수 없습니다!")
if beamMob1 is None:
    raise Exception("[오류] Named Selection 'Beam_Mobile_1' 을 찾을 수 없습니다!")

print("=" * 55)
print("[STEP 1] Named Selections 확인")
print("  - {}".format(beamRef1.Name))
print("  - {}".format(beamMob1.Name))


# ══════════════════════════════════════════════════════════════════
# STEP 2 : Connections - Beam 생성
# ══════════════════════════════════════════════════════════════════
connections = model.Connections
beam = connections.AddBeam()

# 물성 설정
beam.Material = "Structural Steel"

# 반경 설정 : 0.2 in
beam.Radius = Quantity(0.2, "in")

# Reference / Mobile Named Selection 지정
beam.ReferenceLocation = beamRef1
beam.MobileLocation    = beamMob1

print("=" * 55)
print("[STEP 2] Connections - Beam 생성 완료")
print("  Material  : Structural Steel")
print("  Radius    : 0.2 in")
print("  Reference : {}".format(beamRef1.Name))
print("  Mobile    : {}".format(beamMob1.Name))


# ══════════════════════════════════════════════════════════════════
# STEP 3 : 두 Named Selection 간 거리 측정
#           (BoundingBox 중심점 기준, ANSYS 내부 단위 = m)
# ══════════════════════════════════════════════════════════════════
def get_ns_centroid(ns):
    """
    Named Selection 내 모든 지오메트리 엔티티의
    BoundingBox 중심점 평균을 반환합니다. (m)
    """
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
        print("  [경고] BoundingBox 접근 오류 - {}".format(ex))

    if count == 0:
        return None
    return (x_sum / count, y_sum / count, z_sum / count)

ref_pt = get_ns_centroid(beamRef1)
mob_pt = get_ns_centroid(beamMob1)

if ref_pt is None or mob_pt is None:
    raise Exception("[오류] Named Selection 중심 좌표를 계산할 수 없습니다!")

dx = ref_pt[0] - mob_pt[0]
dy = ref_pt[1] - mob_pt[1]
dz = ref_pt[2] - mob_pt[2]
distance_m = math.sqrt(dx*dx + dy*dy + dz*dz)   # 단위 : m
distance_in = distance_m * 39.3700787             # 참고용 인치 환산

print("=" * 55)
print("[STEP 3] 거리 측정 결과")
print("  Beam_Reference_1 중심 : ({:.6f}, {:.6f}, {:.6f}) m".format(*ref_pt))
print("  Beam_Mobile_1    중심 : ({:.6f}, {:.6f}, {:.6f}) m".format(*mob_pt))
print("  거리 (m)  : {:.6f} m".format(distance_m))
print("  거리 (in) : {:.6f} in (참고)".format(distance_in))


# ══════════════════════════════════════════════════════════════════
# STEP 4 : Automation - Object Generator 설정 및 실행
# ══════════════════════════════════════════════════════════════════
dist_min_m = distance_m - 0.01   # 측정값 - 0.01 m
dist_max_m = distance_m + 0.01   # 측정값 + 0.01 m

# Object Generator 는 Beam 오브젝트의 자식으로 추가됩니다.
objGen = beam.AddObjectGenerator()

# ── Reference / Mobile Named Selection 이름 접두어 ──────────────
#    "Beam_Reference" 를 prefix 로 지정하면
#    Beam_Reference_1, Beam_Reference_2 … 를 자동으로 탐색합니다.
objGen.ReferenceNamedSelectionPrefix = "Beam_Reference"
objGen.MobileNamedSelectionPrefix    = "Beam_Mobile"

# ── 거리 필터 (m 단위) ─────────────────────────────────────────
objGen.MinimumDistance = Quantity(dist_min_m, "m")
objGen.MaximumDistance = Quantity(dist_max_m, "m")

# ── Ignore Original 체크 ──────────────────────────────────────
objGen.IgnoreOriginal = True

print("=" * 55)
print("[STEP 4] Object Generator 설정")
print("  Reference Prefix : Beam_Reference")
print("  Mobile Prefix    : Beam_Mobile")
print("  Min Distance     : {:.6f} m  ({:.4f} - 0.01)".format(dist_min_m, distance_m))
print("  Max Distance     : {:.6f} m  ({:.4f} + 0.01)".format(dist_max_m, distance_m))
print("  Ignore Original  : True")

# ── Generate 실행 ─────────────────────────────────────────────
objGen.Generate()

print("=" * 55)
print("[완료] Object Generator 실행 완료!")
print("=" * 55)
