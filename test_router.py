#!/usr/bin/env python3
"""안전 게이트 검증 — 마이크·클로드 없이 로직만 확인.

위험 명령 게이트는 오작동하면 사고로 이어지므로 회귀 테스트를 붙여둔다.
    python test_router.py
"""
import subprocess

import router

# 테스트가 실제로 볼륨을 바꾸거나 5분 뒤 타이머를 울리면 안 된다.
_spawned = []
router._osa = lambda script: _spawned.append(("osa", script))
subprocess.Popen = lambda *a, **k: _spawned.append(("popen", a[0]))  # type: ignore[assignment]

FAIL = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}\n    기대={want!r}\n    실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


print("\n[1] 호출어 인식")
for said, want in [
    ("동백아, 오늘 일정 뭐야", "오늘 일정 뭐야"),
    ("동백아 배포해줘", "배포해줘"),
    ("동백 로그 확인해", "로그 확인해"),
    ("똥백아, 매출 알려줘", "매출 알려줘"),      # whisper 오인식 변형
    ("돈백아 상태 어때", "상태 어때"),
    ("dongbaek 상태 알려줘", "상태 알려줘"),
    ("안녕하세요 반갑습니다", None),
    ("그냥 혼잣말 중입니다", None),
    ("어제 동백한테 말했는데", None),   # 앞부분에 없으면 무시
    # 긴 형태가 먼저 매칭돼야 호격 '아'가 명령에 섞이지 않는다
    ("동백아 배포", "배포"),
]:
    check(f"{said!r}", router.match_wake(said), want)

print("\n[1a] 초성만 흔들린 오기 — 목록에 없어도 뼈대로 잡는다")
# 전부 사장님이 '동백아' 를 부르신 것인데 whisper 가 초성을 놓친 실측 오기다.
# 목록에 하나씩 넣어 쫓아다니던 것을 모음·받침 뼈대 일치로 대신한다.
for said, want in [
    ("공개가 오늘 일정 알려줘", "오늘 일정 알려줘"),
    ("공대가 지금 뭐해", "지금 뭐해"),
    ("동재가, 매출 알려줘", "매출 알려줘"),
    ("홍백아 상태 어때", "상태 어때"),
    ("콩백아 로그 봐줘", "로그 봐줘"),          # 목록에 없는 새 오기
    # ⚠ 받침 한 글자가 다르면 남의 말이다. 뼈대를 통째로 보는 이유.
    ("동생아 밥 먹었어", None),
    ("담배가 어디 갔지", None),
    ("동기가 연락했어", None),
    ("동거가 시작됐대", None),
]:
    check(f"{said!r}", router.match_wake(said), want)

print("\n[1a-2] '동백' 은 지명이기도 하다 — 아파트 방송에 말대꾸한 실사례 (08-13 19:37)")
for said, wake in [
    ("동백 1동 행동복지센터에서 만내말씀드립니다", False),   # 실사례 방송
    ("동백동 주민 여러분께 안내드립니다", False),
    ("동백지구 아파트 공사 안내", False),
    ("동백역 방면 열차가 들어오고 있습니다", False),
    ("동백아 역까지 얼마나 걸려", True),                    # 호격 부름은 산다
    ("동백 로그 확인해", True),                             # 민꼬리 + 일반 명령도 산다
]:
    check(f"{said[:24]!r} → {'부름' if wake else '지명'}",
          router.match_wake(said) is not None, wake)

print("\n[1b] ⚠ 이름만 부른 것 vs 짧은 명령 — 길이로 재면 안 된다")
# 예전에는 '꼬리가 3자 이하면 호명' 이었다. '뭐라고'·'몇시야'·'왜' 가 전부
# 3자 이하라, 분명한 질문이 "네" 로 되돌아왔다 ("동백이 뭐라고?" → "네").
# 짧은 게 문제가 아니라 '군말인가' 가 문제다.
for said, bare, why in [
    ("동백아", True, "이름만"),
    ("동백아?", True, "이름만 + 물음표"),
    ("동백아 저기", True, "군말"),
    ("동백아 어", True, "군말 한 글자"),
    ("동백아 음", True, "whisper 잡음"),
    ("동백이 뭐라고?", False, "실제로 겪은 사례 ← 핵심"),
    ("동백아 몇시야", False, "3자지만 명령"),
    ("동백아 왜?", False, "1자지만 질문"),
    ("동백아 뭐야", False, "2자지만 질문"),
    ("동백아 오늘 일정 알려줘", False, "평범한 명령"),
]:
    rest = router.match_wake(said)
    check(f"{said!r} → {'호명' if bare else '명령'} ({why})",
          router.is_bare_call(rest or ""), bare)

print("\n[1c] 되읽기 — '방금 뭐라고?' 는 Claude 에 물을 일이 아니다")
import config  # noqa: E402
config.DAWN_FAR_ENABLED = False   # 시간 무관 검증 — 새벽 완화는 test_dawn 이 검증
import speak  # noqa: E402

speak._play = lambda body: None            # 소리 없이 마지막 답변만 기록
# 되읽기는 셋 다 만족해야 쓸모가 있다: 게이트 통과 + 로컬 처리 + 답변만 되읽기.
for said in ["뭐라고?", "뭐라구", "다시 말해줘", "못 들었어", "다시 한번", "안 들려"]:
    check(f"{said!r} → 승인 안 물음", router.danger_hit(said), None)
    check(f"{said!r} → 로컬 처리 (토큰 0)", router.handle_local(said) is not None, True)

# ⚠ 부분일치로 잡으면 발송 명령이 조회로 새어 나간다. 실제로 한 번 그렇게
#   만들었다가 "메일 뭐라고 보낼까" 가 게이트를 통과했다.
for said in ["메일 뭐라고 보낼까", "그게 뭐라고 생각해?", "그 파일 뭐라고 적혔는지 지워줘"]:
    check(f"{said!r} → 되읽기 아님", router.is_repeat_request(said), False)
    check(f"{said!r} → 게이트로 감", bool(router.danger_hit(said)), True)

# 되읽는 것은 '답변' 이어야 한다. 알림("네, 확인하고 있습니다")을 되읽으면 안 된다.
speak.say("일정은 세 건 있습니다.", block=True)
speak.say(config.ACK_MESSAGE, block=True, priority=speak.PRIORITY_NOTICE)
import time as _t; _t.sleep(0.2)
check("되읽기는 답변만 — 알림은 제외",
      "일정은 세 건" in router.handle_local("뭐라고?"), True)

print("\n[1d] 전화 모드 — 통화와 동백 호출을 구분한다")
for said, hold in [
    ("전화 받을게", True),
    ("전화 모드", True),
    ("통화 중이야", True),
    ("잠깐만 쉬어", True),
    ("김상무한테 전화해줘", False),          # 행위 — 닫으면 안 된다
    ("전화 걸어줘", False),
    ("통화중에 들은 건데 메일 보내줘", False),  # 부분일치로 잡으면 이게 샌다
]:
    check(f"{said!r} → {'귀 닫기' if hold else '평소 명령'}",
          router.is_hold_request(router.normalize(said)), hold)
for said, bare in [
    ("여보세요", True),
    ("전화 왔다", True),
    ("여보세요 김상무님", False),           # 문장에 섞이면 인용일 수 있다
]:
    check(f"{said!r} → 자동 감지 {bare}", router.is_bare_hold(router.normalize(said)), bare)
for said, res in [
    ("다시 들어", True),
    ("전화 끝났어", True),
    ("전화모드 해제", True),
    ("다시 들어봐 그 노래", False),
]:
    check(f"{said!r} → 해제 {res}", router.is_resume_request(router.normalize(said)), res)

print("\n[1e] ⚠ 통화 가로채기 방지 — 김철수 통화가 명령이 됐던 실사례")
for said, mine in [
    ("너한테 한 얘기 아니야", False),
    # ⚠ 한글 음절 함정 — "아닌데" 에는 "아니" 가 없다(아+닌+데). 이걸로 놓쳤다.
    ("너한테 한 말이 아닌데", False),
    ("너한테 하는 말 아냐", False),
    ("확인하지 마 너한테 한 얘기 아니야", False),
    ("너 안 불렀고 공부하고 있으니까", False),
    ("지금 통화중이야", False),
    ("너랑 얘기하는 거 아니야", False),
    ("동백아 오늘 일정 알려줘", True),        # 정상 명령은 그대로
    ("메일 확인해줘", True),
]:
    got = router.is_not_for_you(router.normalize(said))
    check(f"{said!r} → {'내 말 아님' if not mine else '정상 명령'}", got, not mine)

# ⚠ 긴 문장 오판 실사례 (2026-08-12 23:02) — 아니·너랑·얘기 가 한 문장에
#   우연히 다 들어가 전화 모드 10분. 사장님이 오판을 항의하신 말이었다.
#   조합 판정은 짧은 말에만 쓴다. 명시 문구("통화중이야")는 길어도 잡는다.
for said, mine in [
    ("아니 통화가 끝이 났는데 통화가 끝이 났는데 너랑 얘기를 하는데 왜 자꾸 "
     "니가 내 말을 조금만 길게 하면 그걸 다른 걸로 오해해가지고 너한테 하는 "
     "얘기인지 모르는 거야", True),                    # 항의는 명령이다
    ("아니 근데 너랑 얘기하다 보니까 아까 그 광고 얘기 말인데 그거 다시 한번 "
     "정리해서 알려줄래", True),                       # 긴 정상 명령
    ("잠깐만 나 지금 통화중이야 이따가 다시 부를게 미안한데 좀 있다 얘기하자",
     False),                                          # 명시 문구는 길어도 잡음
]:
    got = router.is_not_for_you(router.normalize(said))
    check(f"긴 문장 {said[:24]!r}… → {'내 말 아님' if not mine else '정상 명령'}",
          got, not mine)

# 승인까지 물으면 우스운 일이 된다 — 텔레그램에서 실제로 그랬다.
for said in ["너한테 한 말이 아닌데", "너한테 한 얘기 아니야", "통화중이야"]:
    check(f"{said!r} → 승인 안 물음", router.danger_hit(said), None)

print("\n[1i] 성능 통계는 동백 얘기일 때만 — '미팅 장소까지 얼마나 걸려?' 가")
print("     '오늘 165건 처리했습니다' 로 답한 실사례 (2026-08-13 12:24)")
for said, perf in [
    ("여기서 오늘 미팅 장소까지 얼마나 걸려", False),
    ("공항까지 얼마나 걸려", False),
    ("거기까지 차로 얼마나 걸려", False),
    ("니 응답 속도 얼마나 걸려", True),
    ("빠르기 어때", True),
    ("요즘 왜 이렇게 느려", True),
]:
    check(f"{said!r} → {'성능' if perf else '일반'}",
          router._is_perf_query(router.normalize(said)), perf)

print("\n[1j] 의미 완결 판정 (H1) — 완결이 분명하면 덜 기다린다")
for said, done in [
    ("오늘 일정 알려줘", True),
    ("광고플랫폼 성과 어때", True),
    ("내일 미팅 잡아 줄래", True),
    ("어제 매출 얼마였어", True),
    ("메일 보내는 거 취소해줘", True),
    ("그리고", False),                 # 미완결 — 더 기다려야 한다
    ("이번 달 광고비를", False),
    ("확인해서", False),
    ("내가 말하려는 건", False),
]:
    check(f"{said!r} → 완결 {done}", router.looks_complete(said), done)

print("\n[1k] 맞장구 판정 (H3) — 동백 말 중의 '네·그래' 는 명령이 아니다")
for said, bc in [
    ("네", True),
    ("네, 네. 어?", True),
    ("응 그래", True),
    ("오케이 좋아", True),
    ("알겠습니다", True),
    ("네 일정 알려줘", False),         # 맞장구 뒤에 명령이 붙으면 명령이다
    ("계속해", False),                 # 무시하면 안 되는 요청
    ("그래서", False),
    ("어 그거 있잖아", False),
]:
    check(f"{said!r} → 맞장구 {bc}",
          router.is_bare_backchannel(router.normalize(said)), bc)

print("\n[1e-3] 목소리 등록 요청 vs 상태 질문 — '기억하고 있는 거지?' 에")
print("        '기억했습니다' 로 답하면 질문이 영영 답을 못 받는다 (08-13 실사례)")
for said, want in [
    ("지금 목소리 기억해", True),
    ("지금 목소리 나라고, 기억하라고", True),          # '라고' 는 강조지 인용이 아니다
    ("지금 목소리 내 목소리니까 등록해놔", True),
    ("기존 목소리도 다 기억하고 있는 거지", False),     # 상태 질문 ← 핵심
    ("내 목소리 기억하고 있어", False),
    ("목소리 저장돼 있지", False),
    ("내 목소리 기억할까", False),
]:
    check(f"{said!r} → {'등록' if want else '질문'}",
          router.is_voice_enroll_request(router.normalize(said)), want)

print("\n[1e-2] 목소리 오인 정정 — 놓치면 90초 뒤 되살릴 발화가 사라진다")
for said, fix in [
    # ⚠ 전체일치만 받다가 놓친 실사례 (2026-08-12)
    ("지금 얘기하는 것도 나야", True),
    ("지금 얘기하고 있는 것도 나야", True),
    ("아까 그것도 나야", True),
    ("방금 나야", True),
    ("이것도 나야", True),
    ("내 목소리인데", True),
    # 정정을 '인용' 하는 말은 명령이지 정정이 아니다
    ("방금 나야 라고 했는데", False),
    # 남 얘기는 지금 이 발화를 가리키지 않는다
    ("어제 강남에서 만난 사람은 나야", False),
    ("오늘 일정 뭐 있어", False),
]:
    check(f"{said!r} → {'정정' if fix else '정정 아님'}",
          router.is_voice_correction(router.normalize(said)), fix)

print("\n[1f] whisper 잡음은 아예 보지 않는다")
for said, noise in [
    ("문문문문문문문문", True), ("롱, 롱, 롱.", True),
    ("다윗, 다윗, 다윗, 다윗.", True), ("동.", True),
    ("동백아 몇시야", False), ("오늘 일정 알려줘", False),
]:
    check(f"{said[:16]!r} → {'잡음' if noise else '사람 말'}", router.is_noise(said), noise)

print("\n[1g] 일정은 등록·수정·삭제 모두 승인 없이 (사장님 지시 2026-08-12)")
for said, need in [
    ("내일 3시에 미팅 잡아줘", False),
    ("캘린더에 회의 등록해", False),
    ("내일 오전 10시에 승례문 피팅 일정 잡아줘", False),
    # "내가 등록하라 해서 등록하는 거고 수정하라 해서 수정하는 걸텐데."
    ("그 일정 취소해", False),
    ("내일 미팅 삭제해줘", False),
    # ⚠ 캘린더 밖의 삭제는 그대로 승인을 받아야 한다. 여기가 무너지면
    #   '일정' 두 글자만 섞어도 파일이 지워진다.
    ("그 파일 삭제해", True),
    ("데이터베이스 삭제해", True),
    ("일정 잡고 프로덕션 배포해줘", True),   # 다른 위험이 섞이면 승인
]:
    check(f"{said!r} → {'승인' if need else '바로'}",
          router.danger_hit(said) is not None, need)

print("\n[2] 위험 명령 탐지 (걸려야 함)")
for said in [
    "프로덕션 배포해줘",
    "그 파일 삭제해",
    "깃 푸시해",
    "디비 마이그레이션 돌려",
    "블로그 글 발행해",
    "고객한테 메일 보내줘",
    "브랜치 강제로 리셋해",
    "환경 변수 삭제해",
    # MCP 로 실제 계정을 건드리는 것들
    # (일정은 등록·수정·삭제 전부 승인에서 빠졌다 — 위 [1g] 참조)
    "이 메일에 답장해줘",
    "메일 초안 작성해",
    "참석 수락해",
]:
    hit = router.danger_hit(said)
    check(f"{said!r} → 차단", hit is not None, True)

print("\n[3] 안전 명령 (통과해야 함)")
for said in [
    "오늘 일정 뭐야",
    "레일웨이 로그 보여줘",
    "이 코드 리뷰해줘",
    "지금 몇 시야",
    "매출 요약해줘",
]:
    hit = router.danger_hit(said)
    check(f"{said!r} → 통과", hit, None)

print("\n[4] 승인 판정 (통과해야 함) — 전부 로컬 정규식, 토큰 0")
for said in ["진행", "진행해", "진행해줘", "진행합시다", "승인", "실행해줘",
             "그래", "오케이", "좋아", "예스", "고고", "네 진행해"]:
    check(f"{said!r} → 승인", router.is_confirmation(said), True)

print("\n[5] ⚠ 승인 거부 — 부정어가 항상 이겨야 함")
for said in [
    "아니 취소",
    "진행하지마",          # 승인어+부정어 동시 → 부정 우선
    "진행하지 마",
    "그래 근데 나중에",     # '그래' 있지만 '나중에'
    "오케이 잠깐",         # '오케이' 있지만 '잠깐'
    "승인 안 해",
    "실행 말고",
    "됐어",
    "멈춰",
    "네",                  # 애매한 대답은 승인이 아니다
    "어",
    "",
]:
    check(f"{said!r} → 취소", router.is_confirmation(said), False)

print("\n[2b] ⚠ 실제로 게이트를 빠져나갔던 문장들")
# 로그에서 발견한 실제 사례. 이것들이 안 걸려서 Claude 를 평상시 권한으로
# 불렀고, 쓰기가 막힌 채 1,000원씩 태웠다.
for said in [
    "음성코드 겹치는거 알림 수정해줘.",   # 끝의 마침표가 $ 앵커를 깨뜨림
    "음성으로도 고칠 수 있게 해줘.",      # 명사 없이 동사만
    "이거 좀 고쳐줘",
    "그 부분 바꿔줘.",
    "설정 변경해줘!",
    "리팩토링 좀 해줘",
]:
    truthy = router.danger_hit(said)
    check(f"{said!r} → 게이트", truthy is not None, True)

print("\n[5a] whisper 오인식 — 관측된 표기는 승인으로 인정")
for said in ["신용", "신용!", "진앵", "진행헤", "실앵해", "오케잉"]:
    check(f"{said!r} → 승인", router.is_confirmation(said), True)

print("\n[5a-2] ⚠ 오인식 보정이 거부를 뒤집으면 안 됨")
for said in ["신용 안 해", "진앵 하지마", "오케잉 취소", "아니", "그만해",
             "다음에", "패스", "몰라", "무슨 소리야"]:
    check(f"{said!r} → 취소 유지", router.is_confirmation(said), False)

print("\n[5a-3] '못 알아들음' 과 '거부' 는 다르게 취급")
# 못 알아들으면 되묻고, 거부면 바로 끝낸다. 이 구분이 없으면
# whisper 오인식 때 조용히 취소되어 '왜 막히지' 가 된다.
for said, rejected in [
    ("아니 됐어", True),
    ("취소해", True),
    ("나중에", True),
    ("어버버", False),      # 못 알아들음 → 되물어야 함
    ("음…", False),
    ("뭐라고", False),
]:
    check(f"{said!r} → 거부={rejected}", router.is_rejection(said), rejected)

print("\n[5b] 일정: 조회는 로컬로, 쓰기는 게이트로 가야 함")
for said, want_local in [
    ("이번 주 일정 뭐야", True),
    ("오늘 일정 알려줘", True),
    ("다음 주 미팅 있어?", True),
    ("내일 약속 뭐 있지", True),
    # 쓰기 동사가 섞이면 로컬이 가로채면 안 된다 (위험 게이트로 가야 함)
    ("내일 3시에 일정 잡아줘", False),
    ("그 일정 삭제해", False),
    ("캘린더에 회의 등록해", False),
]:
    got = router._is_schedule_query(said)
    check(f"{said!r} → {'로컬조회' if want_local else '게이트'}", got, want_local)

print("\n[5c] 기간 해석")
for said, want in [("오늘 일정", 1), ("내일 일정", 2), ("이번 주 일정", 7),
                   ("다음 주 일정", 14), ("이번 달 일정", 30)]:
    check(f"{said!r} → {want}일", router._schedule_days(router.normalize(said)), want)

print("\n[6] 로컬 처리 (0 토큰)")
for said, should_be_local in [
    ("지금 몇 시야", True),
    ("오늘 며칠이야", True),
    ("5분 타이머 맞춰줘", True),
    ("볼륨 올려줘", True),
    ("매출 분석해줘", False),
    # 시각·날짜 단어가 있어도 문장에 다른 내용이 붙으면 추론 질문이다.
    # "몇시" 부분일치로 시계를 읽어줬다가 엉뚱한 답이 나간 실사례.
    ("내일 미팅이 오후 2시야 집에서 몇 시에 나가야 해", False),
    ("출장 날짜가 언제였지", False),
]:
    got = router.handle_local(said)
    check(f"{said!r} → {'로컬' if should_be_local else '클로드'}", got is not None, should_be_local)

print("\n[6b] 일정 조회 vs 추론 질문")
for said, want_local in [
    ("내일 일정 알려줘", True),
    ("내일 미팅 몇 시야", True),
    ("내일 미팅 가려면 몇 시에 출발해야 해", False),
    ("미팅 늦지 않으려면 언제 나가야 해", False),
]:
    check(f"{said!r} → {'로컬조회' if want_local else '클로드'}",
          router._is_schedule_query(said), want_local)

print("\n[7] 보류 — 승인·거부가 아닌 세 번째 대답")
for said, want in [
    ("조금만 더 기다려주세요", True),
    ("잠깐만", True),
    ("오케이 잠깐만", True),          # 승인어가 섞여도 보류가 이긴다
    ("이따가 하자", True),
    ("잠깐 아니다 취소", False),      # 취소어가 섞이면 취소가 이긴다
    ("진행해", False),
    ("취소해", False),
]:
    check(f"is_hold({said!r}) = {want}", router.is_hold(said), want)

for said, want_confirm in [
    ("오케이 잠깐", False),           # 보류 섞임 → 승인 아님
    ("좋아 진행해", True),
    ("잠깐 진행하지 마", False),
]:
    check(f"is_confirmation({said!r}) = {want_confirm}",
          router.is_confirmation(said), want_confirm)

print("\n[6c] 특정 하루 일정 — 요일을 물으면 그 날만")
from datetime import date, timedelta   # noqa: E402

_today = date.today()
for said, want in [
    ("오늘 일정", _today),
    ("내일 일정", _today + timedelta(days=1)),
    ("모레 일정", _today + timedelta(days=2)),
    # 요일을 물었는데 일주일치를 다 읽어주던 문제
    ("목요일 미팅 알려줘", _today + timedelta(days=(3 - _today.weekday()) % 7)),
    ("다음 주 목요일 일정", _today + timedelta(days=(3 - _today.weekday()) % 7 + 7)),
    # 기간 조회는 하루로 좁히지 않는다
    ("이번 주 일정 알려줘", None),
    ("일정 알려줘", None),
]:
    check(f"schedule_only_date({said!r})", router.schedule_only_date(said), want)

print("\n[6d] 이름을 대고 물으면 그 일정만")
for said, want in [
    # "강남 미팅 언제야" 에 전체 일정을 읽어주던 문제
    ("우리 강남 미팅이 언제야", "강남"),
    ("강남에서 미팅 언제야", "강남"),          # 조사가 붙어도
    ("농특위 회의 언제지", "농특위"),
    ("도도커뮤니케이션 미팅 언제야", "도도커뮤니케이션"),   # 한 글자도 깎이면 안 된다
    # 전체를 물은 것까지 이름으로 오해하면 안 된다
    ("오늘 일정 알려줘", None),
    ("이번 주 일정 뭐 있어", None),
    ("내일 미팅 몇 시야", None),
    ("오늘 몇 시에 미팅이야", None),
    ("다음 주 목요일 일정", None),
]:
    check(f"schedule_keyword({said!r})", router.schedule_keyword(said), want)

print("\n[7b] 목소리 등록 요청 — 이름 뽑기")
for said, want in [
    ("목소리 등록해줘 이름은 김철수", "김철수"),
    ("김철수 목소리 등록해줘", "김철수"),
    ("김철수님 음성 추가해줘", "김철수"),      # 호칭은 이름에서 뗀다
    ("내 목소리 등록해줘", ""),                # 이름을 안 말했으면 되묻는다
    ("화자 등록해줘", ""),
    # 등록 요청이 아닌 것까지 삼키면 안 된다
    ("오늘 일정 알려줘", None),
    ("일정 등록해줘", None),
    ("메일 등록해줘", None),
    ("노래 틀어줘", None),
]:
    check(f"enroll_request({said!r})", router.enroll_request(said), want)

for said, want in [
    ("김철수", "김철수"),
    ("이름은 김철수이야", "김철수"),           # 받아쓰기가 조사를 붙여 적는다
    ("김철수이라고 해줘", "김철수"),
    ("영희", "영희"),                          # 짧은 이름의 끝글자를 깎으면 안 된다
]:
    check(f"clean_name({said!r})", router.clean_name(said), want)

print("\n[8] 한빛기획 고유 게이트 — 집행·발행·발신은 걸리고 조회는 통과")
for said, must_gate in [
    ("네이버 광고 집행해", True),
    ("파워링크 예산 올려줘", True),
    ("카카오모먼트 캠페인 중단해", True),
    ("입찰가 500원으로 변경해", True),
    ("세금계산서 발행해줘", True),
    ("홈택스 부가세 신고해", True),
    ("고객사 계정 비밀번호 변경해줘", True),
    ("계약 해지 처리해", True),
    ("인보이스 발송해", True),
    ("알림톡 보내줘", True),
    ("네이버 광고 성과 알려줘", False),
    ("이번 달 광고비 얼마 썼어", False),
    ("세금계산서 발행 내역 보여줘", False),
    ("캠페인 현황 어때", False),
]:
    hit = router.danger_hit(said)
    explicit = hit is not None and hit != router.SAFE_ONLY_REASON
    check(f"{said!r} → {'명시 게이트' if must_gate else '조회 통과'}", explicit, must_gate)

print("\n[성능 질문 판정 — 낱말 하나로 정하지 않는다]")
# "너도 지금 속도가 느린 건 아닌데" 에 성능 보고가 튀어나왔다 (2026-08-14,
# 사장님: "자꾸 동문서답"). 막을 낱말을 늘리는 것으로는 못 끝난다 —
# 이 저장소가 안전 경계에서 이미 배운 것과 같다: 경계를 단어로 긋지 마라.
for s in ("지금 속도 어때", "응답속도 얼마나 걸려", "너 왜 이렇게 느려"):
    check(f"성능 질문이다: {s!r}", router._is_perf_query(s), True)
for s in ("굳이 저걸 뭐냐. QN으로 보낼 이유가 있냐는 거지. 너도 지금 속도가 느린 건 아닌데.",
          "강남 한빛건설까지 얼마나 걸려?",
          "여기서 오늘 미팅 장소까지 얼마나 걸려? 지금 편지 시간 기준으로."):
    check(f"성능 질문 아니다: {s[:26]!r}", router._is_perf_query(s), False)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건\n")
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("✅ 전부 통과")


# ── 제목에 든 낱말이 명령으로 읽히면 안 된다 ───────────────
# 2026-08-12 10:34, 사장님이 텔레그램으로 보내신 말:
#   "오늘 일정에 법무사 대표 변경의 건으로 어 서류 전달 11시 등록해줘"
# '대표 변경의 건' 은 법무 서류 이름인데 '변경' 두 글자가 옮기기 명령으로
# 읽혀, 등록 대신 '일정 찾기' 로 새고 "맞는 일정을 찾지 못했습니다" 로 끝났다.
# 같은 뿌리가 세 곳에 있었다 — _CAL_DESTRUCTIVE, 옮기기 진입, 동사 제거.
print("\n[1h] 제목에 든 '변경·바꿔' 가 명령으로 읽히지 않는다")
for said in [
    "오늘 일정에 법무사 대표 변경의 건으로 어 서류 전달 11시 등록해줘",
    "내일 2시 대표 변경의 건 서류전달 등록해줘",
    "오늘 5시 주소 변경의 건 잡아줘",
]:
    check(f"{said[:24]!r}… → 등록 의도로 본다",
          router.is_calendar_create(said), True)

print("\n[1i] 진짜 옮기라는 말은 여전히 옮기기다")
for said in ["본사 미팅 11시로 옮겨줘", "배움창작소 미팅 4시로 변경해줘"]:
    n = router.normalize(said)
    moving = (not any(w in n for w in ("추가", "생성", "만들", "잡아", "넣어", "등록"))
              and any(w in n for w in ("옮겨", "미뤄", "당겨", "연기해", "변경해", "바꿔줘")))
    check(f"{said!r} → 옮기기", moving, True)
