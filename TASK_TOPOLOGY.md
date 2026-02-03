# LiveWeb Arena - Task Topology

> Comprehensive breakdown of the task space, templates, and variation mechanisms.

## Quick Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        LiveWeb Arena Task Space                             │
├─────────────────────────────────────────────────────────────────────────────┤
│  5 Plugins  →  34 Templates  →  6,579 Combinations  →  ~197M Task IDs      │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ Weather  │   │  Stooq   │   │CoinGecko │   │ Taostats │   │  Hybrid  │
│    6     │   │    7     │   │    8     │   │    10    │   │    3     │
│templates │   │templates │   │templates │   │templates │   │templates │
│          │   │          │   │          │   │          │   │          │
│ wttr.in  │   │stooq.com │   │coingecko │   │taostats  │   │cross-site│
│ weather  │   │ stocks   │   │  crypto  │   │ bittensor│   │  mixed   │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
```

---

## Table of Contents

- [Plugin Summary](#plugin-summary)
- [Template Classification](#template-classification)
- [Detailed Template Taxonomy](#detailed-template-taxonomy)
  - [Weather](#-weather-6-templates)
  - [Stooq](#-stooq-7-templates)
  - [CoinGecko](#-coingecko-8-templates)
  - [Taostats](#-taostats-10-templates)
  - [Hybrid](#-hybrid-3-templates)
- [Task Space Calculation](#task-space-calculation)
- [Architecture Diagram](#architecture-diagram)

---

## Plugin Summary

| Plugin | Templates | Entities | Data Source | Difficulty Range |
|--------|-----------|----------|-------------|------------------|
| **Weather** | 6 | 51 cities, 24 airports | wttr.in | Easy → Hard |
| **Stooq** | 7 | 45 instruments (stocks, indices, forex, commodities) | stooq.com | Easy → Hard |
| **CoinGecko** | 8 | 39 cryptocurrencies | coingecko.com | Easy → Hard |
| **Taostats** | 10 | ~50+ subnets (dynamic) | taostats.io | Easy → Hard |
| **Hybrid** | 3 | 26 mixed assets (crypto + stocks) | Multi-site | Hard only |

---

## Template Classification

### By Difficulty

| Difficulty | Count | Templates |
|------------|-------|-----------|
| **Easy** | 9 | `location_name`, `current_weather`, `astronomy`, `stooq_price`, `coingecko_price`, `coingecko_volume`, `coingecko_rank`, `taostats_subnet_info` |
| **Medium** | 13 | `time_of_day`, `multi_day`, `stooq_currency`, `coingecko_top_movers`, `coingecko_supply`, `coingecko_ath`, `coingecko_performance`, `taostats_ranking`, `taostats_price_change`, `taostats_threshold`, `taostats_delta`, `taostats_range_count`, `taostats_percentage` |
| **Hard** | 12 | `weather_comparison`, `stooq_comparison`, `stooq_ranking`, `stooq_sector_analysis`, `stooq_volatility`, `stooq_range_position`, `coingecko_comparison`, `taostats_comparison`, `taostats_analysis`, `taostats_multi_condition`, `hybrid_top_performer`, `hybrid_ranking`, `hybrid_conditional_branch` |

### By Task Type

| Type | Count | Description |
|------|-------|-------------|
| **Single-hop** | 14 | Direct page visit → extract value |
| **Multi-page** | 10 | Visit multiple pages → compare/aggregate |
| **Computation** | 4 | Extract values → compute derived metric |
| **Aggregation** | 4 | Collect multiple values → average/count |
| **List Navigation** | 2 | Navigate list/table → find specific item |
| **RL-Optimized** | 3 | Cross-site with runtime-determined paths |

### Difficulty × Task Type Matrix

|  | Single-Hop | Multi-Page | Computation | Aggregation |
|--|------------|------------|-------------|-------------|
| **Easy** | `price`, `current_weather`, `subnet_info`, `rank` | - | - | - |
| **Medium** | `time_of_day`, `supply`, `ath`, `currency` | - | `delta`, `percentage` | `multi_day`, `range_count` |
| **Hard** | - | `comparison`, `ranking`, `sector_analysis` | `volatility`, `range_position` | `hybrid_*` |

---

## Detailed Template Taxonomy

### 🌤️ Weather (6 templates)

```
weather/
│
├─ EASY ─────────────────────────────────────────────────────────────────────
│   ├─ current_weather     Real-time: temp, humidity, wind, feels-like
│   ├─ location_name       Forecast: temp high/low, rain chance for date
│   └─ astronomy           Sun/moon times, moon phase
│
├─ MEDIUM ───────────────────────────────────────────────────────────────────
│   ├─ time_of_day         Specific period (morning/afternoon/evening/night)
│   └─ multi_day           2-3 day average or daily breakdown
│
└─ HARD ─────────────────────────────────────────────────────────────────────
    └─ weather_comparison  Compare 2 cities (requires visiting 2 pages)
```

#### Entity Pool: 51 cities across 5 regions

| Region | Count | Examples |
|--------|-------|----------|
| Asia | 12 | Tokyo, Beijing, Seoul, Mumbai, Singapore, Bangkok, Hong Kong, Shanghai, Delhi, Jakarta, Manila, Osaka |
| Europe | 12 | Madrid, Barcelona, Lisbon, Prague, Stockholm, Copenhagen, Oslo, Helsinki, Brussels, Athens, Budapest, Munich |
| Americas | 12 | New York City, Los Angeles, Chicago, Toronto, Mexico City, São Paulo, Buenos Aires, Miami, Seattle, Vancouver, Houston, San Francisco |
| Oceania | 6 | Brisbane, Auckland, Wellington, Adelaide, Canberra, Gold Coast |
| Africa/Middle East | 9 | Dubai, Johannesburg, Cape Town, Tel Aviv, Istanbul, Lagos, Casablanca, Nairobi, Doha |

#### Template Details

| ID | Template | Metrics | Patterns | Variations |
|----|----------|---------|----------|------------|
| 1 | `location_name` | 5 (temp, high, low, rain%, rain?) | 7 | ~1,800 |
| 2 | `time_of_day` | 4 (temp, feels, wind, humidity) × 4 times | 3 | ~4,900 |
| 3 | `multi_day` | 3 (rain?, high, low) × 2 types | 5 variants | ~770 |
| 4 | `current_weather` | 4 (temp, feels, humidity, wind) | 4 | ~820 |
| 5 | `astronomy` | 5 (sunrise, sunset, moonrise, moonset, phase) | 2-3 each | ~510 |
| 6 | `weather_comparison` | 1 (temperature) | 4 | ~60 (15 pairs × 4) |

---

### 📈 Stooq (7 templates)

```
stooq/
│
├─ EASY ─────────────────────────────────────────────────────────────────────
│   └─ stooq_price         Single instrument: price, change %
│
├─ MEDIUM ───────────────────────────────────────────────────────────────────
│   └─ stooq_currency      Convert amount between currencies
│
└─ HARD ─────────────────────────────────────────────────────────────────────
    ├─ stooq_comparison    Compare 2-3 instruments (price/performance)
    ├─ stooq_ranking       Rank 5 instruments, find Nth position
    ├─ stooq_sector_analysis   Compare group averages (3-4 stocks each)
    ├─ stooq_volatility    Derived: (high-low)/close across 5 stocks
    └─ stooq_range_position    Derived: position within daily range
```

#### Entity Pool: 45 instruments

| Type | Count | Examples |
|------|-------|----------|
| US Stocks | 17 | AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, V, WMT, XOM, KO, DIS, NKE, INTC, AMD, COIN |
| Indices | 9 | Dow Jones, S&P 500, NASDAQ 100, FTSE 100, DAX, CAC 40, Nikkei 225, Hang Seng, KOSPI |
| Currencies | 9 | EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD, EUR/GBP, EUR/JPY |
| Commodities | 10 | Gold, Silver, Copper, Crude Oil, Natural Gas, Corn, Wheat, Soybeans, XAU/USD, XAG/USD |

#### Extended Pool for Ranking/Analysis: 48 stocks

| Sector | Count | Examples |
|--------|-------|----------|
| Technology | 14 | AAPL, MSFT, NVDA, GOOGL, META, AVGO, ORCL, CRM, ADBE, AMD, INTC, CSCO, IBM, QCOM |
| Finance | 10 | JPM, V, MA, BAC, WFC, GS, MS, C, AXP, SCHW |
| Consumer | 12 | AMZN, TSLA, WMT, HD, KO, PEP, COST, MCD, NKE, SBUX, DIS, TGT |
| Healthcare | 6 | UNH, JNJ, LLY, PFE, ABBV, MRK |
| Energy/Industrial | 6 | XOM, CVX, CAT, BA, GE, UPS |

#### Template Details

| ID | Template | Description | Variations |
|----|----------|-------------|------------|
| 10 | `stooq_price` | Price/change for single instrument | ~810 |
| 11 | `stooq_comparison` | Compare 2-3 instruments on 5 metrics | ~70,950 |
| 12 | `stooq_ranking` | Rank 5 from group, find Nth by metric | ~500 |
| 13 | `stooq_sector_analysis` | Compare 2 groups of 3-4 stocks | ~77M combos |
| 15 | `stooq_currency` | Convert amount (6 options × 9 pairs × 2 dirs) | ~108 |
| 16 | `stooq_volatility` | Find widest/narrowest (high-low)/close | ~C(48,5)×2 |
| 17 | `stooq_range_position` | Find closest to high/low | ~C(48,5)×2 |

---

### 🪙 CoinGecko (8 templates)

```
coingecko/
│
├─ EASY ─────────────────────────────────────────────────────────────────────
│   ├─ coingecko_price     Price, 24h change, or market cap
│   ├─ coingecko_volume    24h trading volume
│   └─ coingecko_rank      Market cap ranking
│
├─ MEDIUM ───────────────────────────────────────────────────────────────────
│   ├─ coingecko_supply    Circulating/total/max supply
│   ├─ coingecko_ath       All-time high price and date
│   ├─ coingecko_performance   7d/30d/1y returns
│   └─ coingecko_top_movers    Find top gainer/loser
│
└─ HARD ─────────────────────────────────────────────────────────────────────
    └─ coingecko_comparison    Compare 2 coins on metrics
```

#### Entity Pool: 39 cryptocurrencies

| Category | Count | Coins |
|----------|-------|-------|
| Top 10 | 10 | BTC, ETH, USDT, XRP, SOL, BNB, DOGE, USDC, ADA, STETH |
| Top 11-25 | 15 | TRX, AVAX, LINK, SUI, XLM, HBAR, SHIB, DOT, LTC, BCH, UNI, NEAR, APT, ICP, PEPE |
| AI/Compute | 4 | TAO, RENDER, FET, AKT |
| DeFi/L2 | 5 | ARB, OP, POL, AAVE, MKR |
| Other | 5 | ATOM, FIL, GRT, INJ, XMR |

#### Template Details

| ID | Template | Metrics | Variations |
|----|----------|---------|------------|
| 30 | `coingecko_price` | 3 (price, change, mcap) × 4 patterns | ~468 |
| 31 | `coingecko_volume` | 1 × 3 patterns | ~117 |
| 32 | `coingecko_comparison` | C(39,2) pairs × 3 types | ~6,669 |
| 33 | `coingecko_rank` | 1 × 3 patterns | ~117 |
| 34 | `coingecko_top_movers` | 2 (gainer/loser) × 3 patterns | ~150 |
| 35 | `coingecko_supply` | 4 metrics × 3 patterns | ~468 |
| 36 | `coingecko_ath` | 2 (price/date) × 4 patterns | ~312 |
| 37 | `coingecko_performance` | 4 periods × 3 patterns | ~468 |

---

### 🔗 Taostats (10 templates)

```
taostats/
│
├─ EASY ─────────────────────────────────────────────────────────────────────
│   └─ taostats_subnet_info    Query subnet name, price
│
├─ MEDIUM ───────────────────────────────────────────────────────────────────
│   ├─ taostats_ranking        Find subnet at rank N by price
│   ├─ taostats_price_change   Price change (1h/24h/1w/1m)
│   ├─ taostats_threshold      Subnets above/below threshold
│   ├─ taostats_delta          Calculate metric changes
│   ├─ taostats_range_count    Count subnets in value range
│   └─ taostats_percentage     Calculate percentage of totals
│
└─ HARD ─────────────────────────────────────────────────────────────────────
    ├─ taostats_comparison     Compare 2 subnets
    ├─ taostats_analysis       Multi-metric subnet analysis
    └─ taostats_multi_condition    Filter by multiple criteria
```

#### Entity Pool: ~50+ subnets (dynamic)

- Fetched from Taostats API at runtime
- Sorted by emission (default display order)
- All subnets visible on list page

#### Template Details

| ID | Template | Description | Key Variables |
|----|----------|-------------|---------------|
| 20 | `taostats_subnet_info` | Basic subnet info | 2 metrics (name, price) |
| 21 | `taostats_comparison` | Compare 2 subnets | Multiple metrics |
| 22 | `taostats_analysis` | Multi-metric analysis | Complex queries |
| 23 | `taostats_ranking` | Find Nth ranked subnet | 9 positions (2nd-10th) |
| 24 | `taostats_price_change` | Price change over time | 4 periods |
| 25 | `taostats_threshold` | Filter by threshold | Above/below conditions |
| 26 | `taostats_multi_condition` | Multiple filter criteria | Complex logic |
| 27 | `taostats_delta` | Calculate changes | Derived metrics |
| 28 | `taostats_range_count` | Count in range | Aggregation |
| 29 | `taostats_percentage` | Calculate percentages | Computation |

---

### 🔀 Hybrid (3 templates)

> **Cross-site RL-optimized tasks** — These templates are specifically designed to require reinforcement learning approaches, as they cannot be solved by simple supervised fine-tuning.

```
hybrid/
│
└─ HARD (all) ───────────────────────────────────────────────────────────────
    │
    ├─ hybrid_top_performer
    │   │  Find best 24h performer among mixed assets
    │   │  WHY RL: Exploration required, optimization objective
    │   └─ Selection: 2-3 crypto + 2-3 stocks
    │
    ├─ hybrid_ranking
    │   │  Rank 4-5 assets by 24h performance
    │   │  WHY RL: Memory + comparison across sites
    │   └─ Scoring: Kendall tau correlation (partial credit)
    │
    └─ hybrid_conditional_branch
        │  IF crypto_change > threshold THEN stock_A.price
        │  ELIF crypto_change < -threshold THEN stock_B.price
        │  ELSE stock_C.change
        │  WHY RL: Runtime-determined path, cannot demonstrate
        └─ Variables: 11 conditions × 5 pos × 6 neg × 4 neutral × 3 thresholds
```

#### Entity Pool: 26 assets

| Type | Count | Assets |
|------|-------|--------|
| Crypto | 14 | BTC, ETH, USDT, BNB, XRP, SOL, DOGE, ADA, TRX, AVAX, LINK, DOT, LTC, UNI |
| Stocks | 12 | AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA, JPM, V, WMT, XOM, KO |

#### Why These Are RL-Only Tasks

| Template | Why Not SFT | RL Advantage |
|----------|-------------|--------------|
| `top_performer` | Must check all assets, can't know winner in advance | Learn efficient exploration strategies |
| `ranking` | Order depends on real-time data | Learn to remember and compare across visits |
| `conditional_branch` | Path determined at runtime by market data | Learn conditional logic, not fixed sequences |

#### Template Details

| ID | Template | Selection | Variations |
|----|----------|-----------|------------|
| 50 | `hybrid_top_performer` | C(14,2-3) × C(12,2-3) | ~344,000 |
| 51 | `hybrid_ranking` | C(14,2) × C(12,2-3) | ~78,000 |
| 52 | `hybrid_conditional_branch` | 11 × 5 × 6 × 4 × 3 | ~3,960 |

---

## Task Space Calculation

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TASK SPACE CALCULATION                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Template Combinations:                                                     │
│    • Single (1 template):     C(34,1) =        34                          │
│    • Double (2 templates):    C(34,2) =       561                          │
│    • Triple (3 templates):    C(34,3) =     5,984                          │
│    • TOTAL COMBINATIONS:                    6,579                          │
│                                                                             │
│  Task ID Space:                                                             │
│    • Combinations:                          6,579                          │
│    • × Variation seeds:                   × 10,000                          │
│    • = Max task_id:                     65,790,000                          │
│                                                                             │
│  Full Configuration Space:                                                  │
│    • Max task_id:                       65,790,000                          │
│    • × num_tasks options (2,3,4):              × 3                          │
│    • = TOTAL CONFIGURATIONS:          ~197,370,000                          │
│                                                                             │
│  Per-Template Question Variations:                                          │
│    • Weather:     ~4,000 - 12,000 per template                             │
│    • Stooq:       ~800 - 70,000 per template                               │
│    • CoinGecko:   ~120 - 7,000 per template                                │
│    • Taostats:    ~500 - 5,000 per template                                │
│    • Hybrid:      ~30,000 - 350,000 per template                           │
│                                                                             │
│  EFFECTIVE QUESTION SPACE:              BILLIONS+                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Anti-Memorization Mechanisms

| Mechanism | Description | Effect |
|-----------|-------------|--------|
| **Dynamic Data** | Weather, prices, rankings change in real-time | Same question → different answer |
| **Large Entity Pools** | 39-51 entities per plugin | Exponential combinations |
| **Computation Required** | Derived metrics, aggregations | Can't memorize formula outputs |
| **Cross-Site Exploration** | Hybrid templates span multiple domains | Path depends on runtime data |
| **Seed-Based Selection** | Deterministic but unique per seed | Reproducible yet diverse |

---

## Architecture Diagram

```
                              ┌─────────────────┐
                              │  Task Registry  │
                              │   34 Templates  │
                              └────────┬────────┘
                                       │
           ┌───────────┬───────────┬───┴───┬───────────┬───────────┐
           ▼           ▼           ▼       ▼           ▼           │
      ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐  │
      │ Weather │ │  Stooq  │ │CoinGecko│ │Taostats │ │ Hybrid  │  │
      │  6 tpl  │ │  7 tpl  │ │  8 tpl  │ │ 10 tpl  │ │  3 tpl  │  │
      └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘  │
           │           │           │           │           │       │
           ▼           ▼           ▼           ▼           ▼       │
      ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐  │
      │51 cities│ │45 instr.│ │39 coins │ │~50 nets │ │26 assets│  │
      │24 ports │ │48 stocks│ │         │ │(dynamic)│ │(mixed)  │  │
      └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘  │
           │           │           │           │           │       │
           └───────────┴───────────┴─────┬─────┴───────────┘       │
                                         │                         │
                                         ▼                         │
                              ┌─────────────────────┐              │
                              │   Task Generation   │◄─────────────┘
                              │  seed + template(s) │   Combination
                              └──────────┬──────────┘   Selection
                                         │
                                         ▼
                              ┌─────────────────────┐
                              │   CompositeTask     │
                              │  1-4 subtasks       │
                              │  combined_intent    │
                              └──────────┬──────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
              ┌───────────┐        ┌───────────┐        ┌───────────┐
              │ Browser   │        │    GT     │        │ Validator │
              │  Agent    │───────▶│ Collector │───────▶│           │
              │ explores  │ visits │ captures  │ compares│  scores   │
              └───────────┘        └───────────┘        └───────────┘
```

---

## Key Files

| File | Purpose |
|------|---------|
| `liveweb_arena/core/task_registry.py` | Template ID mapping, combination enumeration |
| `liveweb_arena/core/task_manager.py` | Composite task generation |
| `liveweb_arena/plugins/*/templates/*.py` | Template implementations |
| `liveweb_arena/plugins/*/templates/variables.py` | Entity pools and metrics |

---

## Quick Reference

| Metric | Value |
|--------|-------|
| **Plugins** | 5 |
| **Templates** | 34 |
| **Combinations** | 6,579 |
| **Max Task ID** | 65,790,000 |
| **Total Configs** | ~197 million |
| **Difficulty Split** | 9 Easy / 13 Medium / 12 Hard |
