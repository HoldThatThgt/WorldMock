| scenario | 10-08 | 10-09 | 10-10 | 10-11 | 10-12 | mean/day | Δ/day vs i | fresh hit% | $/fresh turn |
|---|---|---|---|---|---|---|---|---|---|
| i  current order, current content (V as run) | 0.2789 | 0.3154 | 0.3385 | 0.3025 | 0.4079 | 0.3286 | +0.0000 | 0.336 | 0.00465 |
| i' current order, new memory, per-decision, slim 进度层 | 0.2838 | 0.2824 | 0.2751 | 0.2237 | 0.3040 | 0.2738 | -0.0548 | 0.396 | 0.00375 |
| ii proposal order, per-decision, V's full 进度层 | 0.2612 | 0.2782 | 0.2823 | 0.2805 | 0.3175 | 0.2839 | -0.0447 | 0.507 | 0.00391 |
| ii proposal order, per-decision, slim 进度层 (P2) | 0.1967 | 0.2020 | 0.1957 | 0.1745 | 0.2196 | 0.1977 | -0.1309 | 0.649 | 0.00250 |
| ii' proposal order, per-second publish, slim | 0.1963 | 0.2012 | 0.1950 | 0.1740 | 0.2189 | 0.1971 | -0.1316 | 0.649 | 0.00250 |
| seg proposal order, per-segment publish, full 进度层 (P1 shape) | 0.2384 | 0.2532 | 0.2567 | 0.2584 | 0.2903 | 0.2594 | -0.0692 | 0.537 | 0.00352 |
| seg' per-segment, slim + segment entries in 进度层 tail (equal content) | 0.2014 | 0.2041 | 0.1986 | 0.1706 | 0.2247 | 0.1999 | -0.1288 | 0.640 | 0.00255 |
| iii 人设,待办(无订单),记忆,工具,环境,进度(+订单), per-decision, slim | 0.1934 | 0.1983 | 0.1874 | 0.1686 | 0.2115 | 0.1919 | -0.1368 | 0.668 | 0.00241 |
