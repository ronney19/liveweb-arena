# Claude Code Memory

## Project Overview

LiveWeb Arena evaluates LLM browser agents on their ability to navigate real websites and extract information.

**Core loop**: Template generates a question → agent browses live websites in a Playwright browser → system collects ground truth (GT) from the same pages the agent visited → validator compares agent answer against GT.

**Key concepts:**
- **Template** (`plugins/<name>/templates/*.py`): Generates questions with deterministic seeds, defines GT extraction logic, and validation rules. Each template targets a specific website/data type.
- **Plugin** (`plugins/<name>/plugin.py`): Wraps a website. Provides `fetch_api_data(url)` to get structured data for any URL on that site, plus domain allowlists and blocked patterns.
- **Page cache**: Each URL is cached as an atomic snapshot of `{html, api_data, accessibility_tree, fetched_at}`. The `api_data` is fetched at the same time as the HTML, so GT and page content share the same data source.
- **GT Collector** (`core/gt_collector.py`): Accumulates GT data as the agent browses. When the agent visits a page, the page's `api_data` is merged into the GT pool following priority rules (see Section 6).
- **Evaluation entry**: `eval.py` — runs templates, launches browser agent, collects GT, validates, and scores.

## Development Guidelines

1. **Occam's Razor** - Keep code minimal while maintaining quality
2. **Engineering First** - Every change should improve overall project structure
3. **Zero Redundancy** - No redundant code allowed
4. **Fix Root Cause** - Never patch over problems, always solve at the root
5. **File Size** - Keep files under 500 lines
6. **Import Style** - Use absolute imports for cross-package (`liveweb_arena.core.xxx`), relative for same package
7. **Commit Rules** - Only commit when explicitly asked; keep messages concise
8. **Template Testing** - Every new template must be tested via `eval.py` with multiple seeds (10-minute timeout)
9. **No Fallback** - All evaluation logic must be deterministic. Never add fallback/default values to mask errors (e.g., `or 0`, `or "N/A"`, `except: return default`). If data is missing or computation fails, raise the error explicitly so the root cause is exposed.

## Template Design Guidelines

**Core Principle**: Templates must test real web interaction ability, NOT memorization.

### 1. Anti-Memorization Design

Fixed question pool + fixed answers = memorizable. Prevent this with:
- **Dynamic data**: Answers that change over time (e.g., counts that grow)
- **Computation required**: Aggregation, comparison, or calculation that cannot be pre-memorized
- **Large entity pools**: Combinatorial space too large to enumerate all Q&A pairs

Avoid templates with small fixed entity sets and static attributes.

### 2. Verifiability

- Clear path must exist: Template -> API endpoint -> Ground truth
- API response and website display must share the same data source
- Validation tolerance covers timing differences and format variations only, not agent errors

### 3. Solvability

- Target website must be publicly accessible without authentication
- Required information must be visible on the page
- **NO navigation hints in questions** - no URLs, symbols, selectors, or shortcuts. Finding the source is part of the test.

### 4. Difficulty Stratification

- **Easy**: Single-hop, direct URL, one data point
- **Medium**: Search required, or multiple data points from same page
- **Hard**: Requires data from multiple pages + computation no single page displays

### 5. Template Validation Checklist

Test with `eval.py` using ONE template and ONE seed. Check in order:

1. **GT Calculation** - GT must return a concrete value. If it errors, the template is broken.
2. **GT Data Source** - GT must use `api_data` from page cache, not independent fetches. Check logs for `[GT] Visit xxx → +N items`.
3. **Data Visibility** - Required data must appear in the page accessibility tree or visible content, not just in the API.
4. **Theoretical Solvability** - A clear navigation path must exist from start URL to answer.

**Interpreting results:**
- Agent fails + GT succeeds = agent capability issue (template is fine)
- GT fails = template is broken (must fix)
- GT uses different data than page shows = data binding issue (must fix)

### 6. Ground Truth Trigger Mechanism

Each page is cached independently with its own `api_data` snapshot. Since list/homepage and detail page caches have different timestamps, the same entity may show different values across pages.

**GT data collection rules (priority: detail > list):**

1. **Page-bound GT**: GT is collected only from pages the agent actually visits. Each page's `api_data` contains all data corresponding to that page.
2. **List page → add new only**: Bulk data from list/homepage adds only entities not yet in the GT pool. Never overwrites existing entries.
3. **Detail page → always overwrite, never be overwritten**: Visiting an entity's detail page overwrites that entity's GT data. Once set by a detail page, subsequent list/homepage visits cannot overwrite it — detail page data has highest priority.
4. **Cross-site isolation**: Different sites cache independently. The same entity on site A vs site B has separate cached data.

**Implication**: Templates requiring multi-entity data should expect the agent to visit each entity's detail page. Detail page visits progressively refine GT data and lock in authoritative values.

## Website Selection Criteria

When adding a new website/plugin to the evaluation system, it must meet these requirements:

### 1. Must-Have Requirements

| Requirement | Rationale |
|-------------|-----------|
| **Public API or structured data** | GT extraction requires reliable data source |
| **No authentication required** | Agents cannot log in |
| **Dynamic data** | Static data enables memorization attacks |
| **Stable page structure** | Frequent redesigns break selectors |
| **No aggressive anti-bot** | Must be accessible via Playwright |

### 2. Evaluation Value

A new website should add **at least one** capability dimension not covered by existing sites:

| Dimension | Current Coverage | Gap |
|-----------|-----------------|-----|
| Numerical comparison | CoinGecko, Stooq, Taostats | ✅ Covered |
| Ranking queries | CoinGecko, Stooq, Taostats | ✅ Covered |
| Cross-site navigation | Hybrid templates | ✅ Covered |
| Time-sensitive events | ❌ None | **Gap** |
| Nested structure navigation | ❌ None | **Gap** |
| Search-driven interaction | ❌ Weak | **Gap** |
| User-generated content | ❌ None | **Gap** |

### 3. Current Website Portfolio

| Website | Domain | Data Type | Update Frequency |
|---------|--------|-----------|------------------|
| **CoinGecko** | Crypto | Price, market cap, volume | Real-time |
| **Stooq** | Finance | OHLC, daily change | Real-time |
| **Weather** (disabled) | Weather | Temperature, forecast | Hourly |
| **Taostats** | Blockchain | Subnet metrics | ~12 seconds |

### 4. Template Quality Standards

Each template must pass these quality gates:

1. **Non-trivial**: Cannot be answered by visiting a single obvious page
2. **Dynamic answer**: Answer changes over time (hours/days)
3. **Computation required**: Needs comparison, aggregation, or logic
4. **Unique capability**: Tests something other templates don't
5. **Theoretically solvable**: Clear navigation path exists

**Anti-patterns to avoid:**
- Questions answerable from homepage alone
- Static fact lookups (e.g., "What year was X founded?")
- Questions requiring data not visible on the page
- Duplicate logic with existing templates
