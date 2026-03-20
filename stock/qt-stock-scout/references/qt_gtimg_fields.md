# qt.gtimg.cn fields (common layout)

The endpoint returns a line like:

`v_sz000625="...~...~...";`

Split the quoted body by `~`. For many A-share tickers the following indices are commonly observed (not guaranteed; treat as best-effort):

- 1: name (GBK)
- 2: code (6 digits)
- 3: last
- 4: prev_close
- 5: open
- 6: volume (raw units from API)
- 7: outer_volume (raw)
- 8: inner_volume (raw)
- 9/10..17/18: buy1..buy5 price/vol pairs
- 19/20..27/28: sell1..sell5 price/vol pairs
- 30: timestamp `YYYYMMDDhhmmss`
- 31: change
- 32: change_pct
- 33: high
- 34: low
- 38: turnover_pct (often)
- 39: pe (often)

If you see unexpected `_fields_len` values or missing indices, rely on the keys produced by `qt_quote.py` rather than assuming a fixed layout.

