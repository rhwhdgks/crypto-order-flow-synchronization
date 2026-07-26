# H2 가격충격 잔차 1분 데이터 가용성 감사

## 목적

미래수익률을 열람하기 전에 1분 aggressor flow, L2 상태, 실행가능 top-10 sweep feature가 같은 UTC 분에 결합되는지 검사했다.

## 파일 감사

- 대상: ADA-USDT 2024-04-08 UTC
- L2 분: 1,440/1,440
- 체결이 존재한 분: 1,328
- L2와 결합된 체결 분: 1,328 (100.00%)
- 무체결 0-flow 분: 112
- 중복 분: 0, 핵심 결측: 0
- point-in-time 위반: 0

## 시점 규칙

- 분 t 시작 직후의 L2 상태와 분 t 동안 확정된 체결 흐름을 사용한다.
- observed impact는 t와 t+1의 midpoint로 계산하며 의사결정은 t+1에만 가능하다.
- 무체결 분은 aggressor flow를 0으로 두고 가격 상태는 L2 midpoint로 유지한다.
- 10,000/50,000/100,000 USDT 명목금액의 top-10 sweep VWAP, impact, fill-rate를 보존한다.

## 결론

모든 고정 gate를 통과했다. H2 protocol과 config를 결과 열람 전에 봉인할 수 있다.
