# Binance-OKX 교차거래소 주문흐름 연구 v1

## 데이터

- 기간: 2024-04-08 포함 ~ 2026-04-08 미포함
- 빈도·자산: 15분, 7자산
- 거래소별 입력 행: 490,560
- Development/OOS: 1년/1년 고정 분할

## 동시 동조화

- 동일 자산 교차거래소 평균 correlation: `0.27669`
- 시간 null 평균: `0.00097`, BH q: `0.002`
- 극단 공통흐름 방향 일치율: `87.19%`
- 시간 null 평균: `50.34%`, event: `5,224`
- 판정: `cross_venue_synchronization_supported`

## 방향성 전파

- 15분 Binance→OKX beta: `-0.01376`
- 15분 OKX→Binance beta: `-0.03774`
- beta 차이 95% bootstrap interval: `[0.00136, 0.04747]`
- 판정: `not_supported`

## 해석 제한

이 연구는 두 거래소의 주문흐름이 같은 시간에 함께 움직이는지와 안정적인 선행 방향이
있는지를 검정한다. 참여자 ID, 미래수익률, 뉴스·커뮤니티·sentiment를 사용하지 않았다.
따라서 intentional herding 또는 거래 가능한 alpha를 식별하지 않는다.
