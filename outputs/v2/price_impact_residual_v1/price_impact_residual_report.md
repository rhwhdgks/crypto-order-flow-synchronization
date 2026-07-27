# H2 가격충격 잔차 OOS 연구

## 설계

- OKX 현물 7개 자산, 1분 L2 및 aggressor flow
- Development 60일에서 모델·95백분위 threshold 고정, OOS 120일 평가
- Primary: catch-up, 15분, 8bps, 자산당 10,000 USDT top-10 sweep
- 뉴스·Reddit·Twitter/X·sentiment·파생상품 미사용

## 데이터

- 분석 행: 1,769,760
- timestamp: 253,440
- Development 적합 행: 564,452
- 고정 event threshold: 0.601408

## OOS 결과

- 1분: 사건 8870건, 평균 -0.1072%, 중앙값 -0.1068%, p=1.0000, q=1.0000
- 5분: 사건 8937건, 평균 -0.1060%, 중앙값 -0.1056%, p=1.0000, q=1.0000
- 15분: 사건 8918건, 평균 -0.1052%, 중앙값 -0.1041%, p=1.0000, q=1.0000
- 30분: 사건 8906건, 평균 -0.1045%, 중앙값 -0.1033%, p=1.0000, q=1.0000

## 판정

- minimum_non_overlap_events: 통과 (events=8918)
- positive_mean_and_median_after_8bps: 실패 (mean=-0.00105177;median=-0.00104111)
- positive_oos_halves: 실패 (half_means=[-0.0010165235326469965, -0.0010870220008421913])
- minimum_assets_positive: 실패 (positive_assets=0)
- primary_top10_capacity_positive: 실패 (capacity_quote=10000;mean=-0.00105177)
- bootstrap_fdr: 실패 (q=1.000000)
- primary_supported: 실패 (all sealed H2 gates passed)

Primary 가설은 지지되지 않았다. 동일 OOS에서 조건을 다시 고르지 않는다.
