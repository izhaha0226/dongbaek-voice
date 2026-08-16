# 동백(Dongbaek) — 맥에 상주하는 한국어 음성 AI 비서

> "동백아, 오늘 일정 뭐야?" — 부르면 0.03초에 답하고, 회의가 시작되면
> 스스로 입을 닫고 받아적고, 끝나면 회의록을 옵시디언에 남기는
> **완전 로컬 우선** 음성 에이전트입니다.

맥미니 한 대에서 24시간 도는 실사용 시스템을 그대로 공개한 것입니다.
데모가 아니라, 매일 쓰면서 실사고를 하나씩 고쳐 온 결과물입니다 —
모든 판정 로직에는 "왜 이렇게 됐는지" 실측 주석이 붙어 있습니다.

## 왜 동백인가 — 장점

**1. 돈이 거의 안 든다 — 3층 두뇌**

명령의 90% 이상은 클라우드에 가지 않습니다.

| 층 | 엔진 | 지연 | 비용 | 담당 |
|---|---|---|---|---|
| 규칙 | 정규식·낱말표 | 0.03초 | 0원 | 시각·일정·날씨·타이머·볼륨·되읽기 |
| 소형 모델 | qwen3:4b (ollama, 로컬) | 0.5초 | 0원 | 잡담 즉답·일정 명령 해석·요약 |
| Claude | 상주 세션 + 스트리밍 | 첫 소리 ~1.5초 | 회당 과금 | 추론·분석·코드 수정 |

같은 질문을 클라우드에 다 보내면 회당 5.7초·$0.03이 드는 것을
실측으로 확인하고 만든 구조입니다.

**2. 대화가 밖으로 안 나간다 — 프라이버시 우선**

받아쓰기(whisper)·화자 인식(ECAPA)·잡담(qwen)·통화 기록·회의록이
전부 이 맥 안에서 처리됩니다. 통화·회의 내용은 클라우드로 보내지
않고 로컬에서 정리해 옵시디언에 남깁니다. 대화 전체는 로컬
SQLite(`state/dongbaek.db`)에 쌓여 "어제 내가 뭐 물어봤지?"에
답할 수 있습니다.

**3. 목소리를 압니다 — 화자 인증 3층**

ECAPA-TDNN 192차원 화자 지문으로 등록된 사람만 명령할 수 있습니다.
절대 문턱 + 등록자 간 격차 + 남-지문 상대비교의 3층이고, 확실한
본인 발화만 골라 스스로 적응 학습합니다. TV·유튜브·옆사람 통화가
명령이 되는 사고를 구조적으로 막습니다.

**4. 사람처럼 대화합니다**

- **끊고 들어오기(barge-in)** — 동백이 말하는 중 말을 걸면 ~0.1초에 입을 다뭅니다
- **억양 인식** — "잘 들려?"의 올라가는 말끝을 피치 추적으로 잡아
  물음표를 복원합니다 (whisper가 빼먹어도 질문으로 처리)
- **의미적 말끝 판정** — "…해줘"처럼 끝난 게 분명하면 빨리 답하고,
  "그리고…"처럼 미완결이면 더 기다립니다
- **맞장구 구분** — 동백 말 중의 "네~", "그래"는 명령이 아니라 경청 신호로 처리
- **다자간** — 대화창에 주인이 있어, 옆사람 말이 내 명령에 섞이지 않습니다
- **원거리 모드** — 새벽에 다른 방에서 부르면 증폭·문턱 완화로 알아듣습니다

**5. 회의·통화 때 눈치가 있습니다**

긴 대화가 감지되면(캘린더에 일정이 있으면 미팅 모드로) **스스로 완전
무음**이 되어 받아적기만 합니다. 30초간 조용하면 끝난 것으로 알아채고,
회의록(핵심 논의·결정·액션아이템)을 Claude가 정리해 옵시디언과
텔레그램으로 보냅니다. "회의 끝났어"라고 말할 필요도 없습니다.

**6. 스스로 자랍니다**

- **스킬 자가생성** — "방금 그거 스킬로 만들어" 한 마디면 직전 문답을
  선언형 스킬 파일로 만들어 다음부터 0원에 즉답합니다. 스킬은 코드가
  아니라 선언이라, 아무리 만들어도 권한이 늘지 않습니다
- **자가 정비** — 3시간마다 자기 오류 로그와 백로그를 보고 코드를 하나씩
  고칩니다. 테스트 독립 재검증·수정량 상한·실패 시 격리 롤백·텔레그램
  보고까지 자동입니다
- **오인식 학습** — 자주 틀리는 고유명사를 스스로 사전에 추가합니다

**7. 폰과 이어져 있습니다**

동백이 대답한 모든 것이 텔레그램으로 미러됩니다. 밖에서는 텔레그램
텍스트·음성 메시지로 같은 동백에게 명령할 수 있습니다.

**8. 안전이 코드로 박혀 있습니다**

위험한 일(발송·삭제·배포)은 복창 후 "진행" 승인을 받아야 하고,
경계는 낱말이 아니라 **도구 권한**으로 긋습니다(조회 등급엔 셸이
없습니다). 코드를 고칠 수 있는 명령은 무조건 스냅샷을 남겨 "되돌려"
한 마디로 복구됩니다. 테스트 45+개가 실사고 하나하나를 회귀로 지킵니다.

## 무엇이 들어 있나

```
음성 파이프라인   audio.py(VAD·barge-in·증폭) → whisper → prosody.py(억양)
두뇌             router.py(규칙) → gatekeeper.py(qwen) → bridge_sdk.py(Claude 상주)
화자             voiceprint.py (ECAPA 3층 + 적응학습 + 오인 정정 "방금 나야")
모드             전화·미팅 모드 (완전 무음·자동 기록·자동 회의록)
기억             dbstore.py(SQLite 전체 대화) + memory_local.py(임베딩 회상)
도구             dongbaek_mcp.py — 캘린더·기억·채점·대화이력을 Claude에 도킹
스킬             skills_local.py + skills/*.md (선언형, 음성으로 생성)
자동화           briefing.py(아침·점심·퇴근) news_local.py(뉴스 카드)
                 self_improve.py(자가 정비) wakeup.py(기상 알람)
연동             telegram_bridge.py · mail_local/mail_mcp(맥 Mail)
                 calendar_local.py(EventKit) · weather_local.py(open-meteo)
안전             권한 3단(config.TOOL_POLICY) · code_guard.py(스냅샷·되돌리기)
```

## 초기 세팅

### 요구 사항

- Apple Silicon 맥 (실사용 기준: 맥미니 M2 급 이상, RAM 16GB+)
- macOS 14+ · Python 3.12 · [uv](https://docs.astral.sh/uv/) (권장)
- [Claude Code CLI](https://claude.com/claude-code) 설치·로그인 (`claude` 명령)
- [ollama](https://ollama.com) + `ollama pull qwen3:4b` (로컬 소형 모델)
- 마이크가 달린 입출력 장치 (실사용: Anker PowerConf — 에코 제거 내장)

### 1. 설치

```bash
git clone https://github.com/yourname/dongbaek-voice ~/dongbaek
cd ~/dongbaek
./setup.sh
```

`setup.sh`가 하는 일: 파이썬 환경 두 개(`.venv` 본체 / `.venv-mcp` MCP 전용
— claude-agent-sdk와 mcp 2.x의 의존성 충돌 때문에 분리)를 만들고,
`templates/`의 자리표시자를 이 맥의 절대경로로 채워 launchd·MCP 설정을
생성합니다. 여러 번 돌려도 안전합니다.

### 2. 개인 설정 — `config_local.py`

```bash
cp config_local.example.py config_local.py
```

열어서 채우세요 (이 파일은 gitignore — 커밋되지 않습니다):
- 호출어 추가·바꾸기 (기본: "동백아")
- 회사·제품 고유명사 (`WHISPER_PROMPT_EXTRA` — 받아쓰기 정확도가 올라갑니다)
- 위험/안전 명령 패턴 추가

API 키류는 macOS 키체인에 둡니다:

```bash
.venv/bin/python secrets_local.py put <이름>
```

### 3. 텔레그램 (선택)

[@BotFather](https://t.me/botfather)로 봇을 만들고:

```bash
.venv/bin/python set_telegram_token.py
```

### 4. 목소리 등록

```bash
.venv/bin/python dongbaek.py --enroll "이름"
```

문장 몇 개를 읽으면 화자 지문이 만들어집니다. 등록하는 순간부터
등록자만 명령할 수 있습니다. 이후에는 음성으로 "내 목소리 등록해놔"
라고 하면 상황별 목소리(아침·먼 거리)를 계속 보탤 수 있습니다.

### 5. 상주 시작

```bash
cp templates/dongbaek.plist ~/Library/LaunchAgents/com.dongbaek.daemon.plist
cp templates/dongbaek-telegram.plist ~/Library/LaunchAgents/com.dongbaek.telegram.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.dongbaek.daemon.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.dongbaek.telegram.plist
```

브리핑·뉴스·자가정비 등 나머지 plist도 같은 방식으로 원하는 것만 올리면
됩니다. 이후 코드 수정 뒤 재시작은 항상:

```bash
./restart.sh
```

(문법 검사 후 데몬+텔레그램을 **동시에** 재시작합니다 — 한쪽만 재시작하면
설정 불일치로 죽습니다. 기동 후 45초쯤 로그를 지켜보세요.)

### 6. 확인

```bash
for f in test_*.py; do .venv/bin/python "$f" || echo "FAIL: $f"; done
tail -f state/daemon.log
```

"동백아" 하고 불러보세요. `들림:`과 `말함:`이 로그에 짝으로 남습니다.

### 처음 겪기 쉬운 문제

| 증상 | 원인·해법 |
|---|---|
| 소리가 안 남 | 기본 출력이 스피커 없는 모니터일 수 있음 — `config.TTS_DEVICE_PREFERENCE` 확인 |
| 헤드리스에서 권한 경고 | `~/.claude.json`의 `hasTrustDialogAccepted`를 켜거나 한 번 대화형으로 `claude` 실행 |
| 캘린더를 못 읽음 | 처음 한 번 터미널에서 실행해 macOS 캘린더 접근을 허용 |
| MCP ImportError | 메일·동백 MCP는 반드시 `.venv-mcp`로 실행 (mcp 2.x 전용) |
| 첫 호명이 느림 | 정상 — whisper 예열 전. 기동 10초 뒤부터 0.4초 수준 |

## 음성 명령 맛보기

```
"동백아, 오늘 일정 뭐야"          → 0원 즉답 (EventKit)
"동백아, 30분 타이머"             → 재생 큐 경유 알림
"동백아, 뭐라고?"                 → 방금 말 되읽기 (클라우드 안 감)
"동백아, 미팅 모드"               → 완전 무음 + 기록 + 자동 회의록
"동백아, 방금 그거 스킬로 만들어"  → 다음부터 0원 즉답
"동백아, 내 목소리 잘 들려?"       → 실측 증폭 배수로 답함
"동백아, 되돌려"                  → 마지막 코드 수정 롤백
```

## 설계 원칙 (코드 전반의 주석이 이 원칙의 실측 기록입니다)

1. **침묵으로 실패하지 않는다** — 못 알아들었으면 못 알아들었다고 말한다
2. **경계는 낱말이 아니라 권한으로** — 오인식은 단어 목록을 뚫는다
3. **조정은 추측이 아니라 실측으로** — 모든 문턱 옆에 측정 로그가 있다
4. **소리가 나간 뒤의 실패는 다시 말하지 않는다** — 겹침이 무음보다 나쁘다
5. **자율은 감사 위에서만** — 자가 수정은 테스트·상한·롤백·보고가 전제다

## 라이선스

MIT
