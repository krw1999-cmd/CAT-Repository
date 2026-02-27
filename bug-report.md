# cat_web Bug Audit Report

**Date:** 2026-02-27
**Method:** Manual read-only audit of main.py, db.py, and all templates

---

## Audit Coverage

Checked systematically:
- All `url_for()` calls in templates vs. route function names in `main.py`
- All template variables vs. what each view function passes via `render_template()`
- All HTMX `hx-swap-oob` targets vs. DOM IDs in parent templates
- DB query SELECT clauses vs. Python field access on results
- `None`/missing guard edge cases
- Orphaned routes

**Result:** One confirmed structural bug found. Everything else checks out.

---

## Bug 1: Duplicate DOM IDs in `payroll_run_builder.html`

**File:** `tools/cat_web/templates/payroll_run_builder.html:140`
**Confidence:** High
**Description:**
The builder template wraps the `_run_payee_totals.html` partial in an outer `<div>` that carries the same `id` as the div inside the partial itself. On initial page load every payee section has two nested elements with the same ID, which is invalid HTML.

**Evidence:**

`payroll_run_builder.html` lines 140–142:
```html
<div id="payee-totals-{{ payee.fee_recipient_id }}">
  {% include "partials/_run_payee_totals.html" %}
</div>
```

`partials/_run_payee_totals.html` lines 1–2:
```html
<div id="payee-totals-{{ payee.fee_recipient_id }}"
     style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:12px">
```

On initial render the DOM contains:
```html
<div id="payee-totals-123">            <!-- outer wrapper, no styles -->
  <div id="payee-totals-123"           <!-- partial, has flex layout styles -->
       style="display:flex;...">
    …
  </div>
</div>
```

**Why it works anyway (but is still wrong):**
The checkbox's `hx-target="#payee-totals-123"` with `hx-swap="outerHTML"` hits the *first* matching element, which is the outer wrapper. HTMX replaces that wrapper with the partial's `<div>`, so after the first toggle the DOM is normalised to a single correctly-styled element. Subsequent toggles work correctly. The JavaScript (`selectAllSplits`, `clearAllSplits`) uses class selectors, not ID selectors, so it's unaffected.

**Impact:**
- Invalid HTML on initial page load (duplicate IDs)
- On the initial render the flex styles live on the inner div, so the header displays correctly, but the outer wrapper div could confuse any tool or browser feature that resolves the ID (`getElementById`, CSS `#id` selectors, accessibility tooling)
- First HTMX toggle normalises the structure, so the bug self-heals on interaction but re-appears on every full page load

**Fix (not applied — review first):**
Remove the wrapper `<div>` from the builder and let the partial render directly:
```html
{# Before #}
<div id="payee-totals-{{ payee.fee_recipient_id }}">
  {% include "partials/_run_payee_totals.html" %}
</div>

{# After #}
{% include "partials/_run_payee_totals.html" %}
```
The HTMX swap target (`#payee-totals-{{ payee.fee_recipient_id }}`) still resolves correctly because the partial's own `<div>` carries that ID.

---

## Summary

- **High confidence:** 1 bug
- **Medium confidence:** 0 bugs
- **Total:** 1

---

## What Was Checked and Found Clean

| Area | Verdict |
|------|---------|
| All `url_for()` calls in templates | ✓ All match existing route functions with correct args |
| `status.html` template variables | ✓ All fields returned by `get_global_status()` |
| `client_view.html` template variables | ✓ All fields returned by `get_client_view_data()` |
| `claims.html` / `get_all_claims()` | ✓ `awaiting_adjuster_count` and `total_collected_sum` both present |
| `_tx_row.html` field access | ✓ All fields present in `get_transactions()` SELECT |
| OOB swap targets in partials | ✓ `#summary-oob`, `#exp-totals-sidebar`, header totals spans all match |
| `payroll_run_builder` toggle OOB targets | ✓ (after first swap — see Bug 1) |
| `run_split_toggle` / `run_exp_split_toggle` returns | ✓ Correctly return `_run_payee_totals.html` with matching ID |
| `get_run_builder_data()` payee fields | ✓ All fields used in builder template present |
| `get_client_view_data()` DB field access | ✓ `client_paid`, `invoice_amount`, etc. all in `get_expenses()` SELECT |
| `get_global_status()` status_notes loop | ✓ `_rows()` returns plain dicts; `.append()` of `status_notes` key is safe |
| Nav `url_for()` calls in `base.html` | ✓ All route names exist |
| Orphaned routes | ✓ None found; all routes reachable via nav, links, or HTMX |
