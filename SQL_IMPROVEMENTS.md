# SQL Performance Improvements Needed

## 1. CRITICAL: Taxonomy Cache Still Uses UNNEST (Line 761-773)

**Problem:** The global taxonomy cache that runs every 2 hours uses `UNNEST(STRING_TO_ARRAY())` to expand delimited values. This causes full table scans and should use the new junction tables instead.

**Location:** `app/crud/company.py` lines 760-774

**Current Code:**
```python
cluster_rows = db.execute(_text(
    "SELECT trim(unnest(string_to_array(tfidf_cluster, '|'))) AS label, COUNT(*) AS cnt"
    " FROM companies"
    " WHERE tfidf_cluster IS NOT NULL AND tfidf_cluster != 'Undefined'"
    " GROUP BY label ORDER BY cnt DESC"
)).fetchall()
```

**Fix:** Use `company_tfidf_clusters` junction table:
```sql
SELECT cluster AS label, COUNT(DISTINCT company_id) AS cnt
FROM company_tfidf_clusters
WHERE cluster != 'Undefined'
GROUP BY cluster
ORDER BY cnt DESC
```

**Same issue for keywords** (line 768-773)

---

## 2. HIGH: Filter Logic Uses ILIKE with No Index (Line 169, 184-186)

**Problem:** Multiple filters use `ILIKE` with patterns that don't benefit from trigram indexes:
- `ai_category.ilike(f"%{t}%")` - full table scan
- `tfidf_cluster.ilike(f"{t}|%")` - can't use B-tree or trigram efficiently

**Location:** `app/crud/company.py` lines 169, 184-186, 197-199, 210, 212, 257, 262, 267

**Impact:** Every list_companies call with these filters causes slow queries

**Fix:** Once junction tables are populated, migrate filters to use them:
```python
# Instead of:
query = query.filter(Company.tfidf_cluster.ilike(f"{t}|%"))

# Use:
from app.models.company_tfidf_cluster import CompanyTfidfCluster
query = query.join(CompanyTfidfCluster).filter(CompanyTfidfCluster.cluster.in_(terms))
```

---

## 3. HIGH: Category Detail Queries Missing Covering Indexes

**Problem:** Queries that filter by category and aggregate scores don't have proper indexes. The composite indexes exist for ai_category but are missing for tfidf_cluster and purpose_keywords.

**Example:** `SELECT AVG(combined_score) FROM companies WHERE tfidf_cluster LIKE '%X%'` - needs index but can't use one due to LIKE pattern.

**Solution:** Once junction tables exist, these queries naturally become fast (index on cluster, join to get scores).

---

## 4. MEDIUM: Count Queries Run Full Table Scans

**Problem:** `count_companies()` applies all filters but doesn't use appropriate indexes. With multiple ILIKE filters, this becomes O(n) per page load.

**Current:** 
- Applies 30+ filter combinations to base query
- Many filters use ILIKE without index support
- No query optimization hints

**Example:** Filtering companies by `tfidf_cluster="X"` AND `canton="ZH"` AND `ai_score > 70` requires:
1. Full table scan to check tfidf_cluster (no index for LIKE)
2. Filter by canton (has index)
3. Filter by score (has index)

**Optimal:** Should use compound index `(tfidf_cluster, canton, ai_score)` but ILIKE prevents this.

---

## 5. MEDIUM: Taxonomy Cache Queries Are Inefficient

**Problem:** `_compute_global_taxonomy()` runs multiple expensive queries:

Line 760-765: UNNEST on tfidf_cluster (full table scan)
Line 768-773: UNNEST on purpose_keywords (full table scan)
Line 776-789: Separate queries for categories, noga_codes, levels, legal_forms, cantons

**Better approach:** Combine into single query with CTEs:
```sql
WITH stats AS (
    SELECT 'ai_category' AS type, ai_category AS value, COUNT(*) AS cnt FROM companies WHERE ai_category IS NOT NULL GROUP BY ai_category
    UNION ALL
    SELECT 'noga_code', noga_code, COUNT(*) FROM companies WHERE noga_code IS NOT NULL GROUP BY noga_code
    UNION ALL
    SELECT 'canton', canton, COUNT(*) FROM companies WHERE canton IS NOT NULL GROUP BY canton
    -- ... etc
)
SELECT * FROM stats ORDER BY type, cnt DESC
```

---

## 6. MEDIUM: OrgCompanyState Joins Can Be Slow

**Problem:** Many queries join to `OrgCompanyState` without batch loading:

Line 645: `get_noga_hierarchy()` does ISOuter join for each query
Line 74-82: `_bulk_org_states()` fetches org-specific states, but called separately

**Improvement:** Pre-load org states in API layer (already done in routes/companies.py:593-598) ✓

---

## 7. LOW: Missing ANALYSE After Migration

**Problem:** After creating junction tables and populating them, PostgreSQL needs to analyze statistics for query planning.

**Solution:** Add to migration 0062 downgrade hook:
```python
op.execute("ANALYSE company_tfidf_clusters")
op.execute("ANALYSE company_purpose_keywords")
```

---

## Implementation Priority

1. **IMMEDIATE (blocks performance):**
   - Update `_compute_global_taxonomy()` to use junction tables
   - Create migration to populate junction tables (already 0062)

2. **SHORT TERM (fixes category queries):**
   - Update `_apply_filters()` to use junction tables for delimited columns
   - Update `_compute_category_stats()` to use junction tables (already done)

3. **MEDIUM TERM (optimization):**
   - Consolidate taxonomy queries with CTEs
   - Add query plan analysis and ANALYSE commands

4. **LONG TERM (refactor):**
   - Consider materialized view for global taxonomy (refresh every 2h)
   - Pre-compute category stats in background job

---

## Testing

After applying these changes, verify:
```bash
# 1. Check category stats load time
curl http://localhost/api/v1/companies/category-stats?type=tfidf_cluster&value=Consulting

# 2. Check list_companies with cluster filter
curl http://localhost/api/v1/companies?tfidf_cluster=Consulting&page=1

# 3. Check NOGA hierarchy
curl http://localhost/api/v1/companies/noga-hierarchy

# 4. Check taxonomy endpoint
curl http://localhost/api/v1/companies/taxonomy
```

All should respond in <100ms with proper indexes and junction tables.
