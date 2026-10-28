#!/bin/zsh
# Where the sales-volume crawl stands: coverage, freshness, and the log tail.
#   pipeline/sales_status.sh
set -u
DB=$(dirname "$0")/../dotnet/API/Data/cards/pricecharting.db
LOG=$HOME/Library/Logs/tcg-predictor/sales-crawl.log
sqlite3 -readonly "$DB" "
  SELECT '  cards with sales history: ' || count(*)
       || '   fresh (<7d): ' || sum(fetched_at >= datetime('now','-7 day'))
       || '   fresh (<30d): ' || sum(fetched_at >= datetime('now','-30 day'))
  FROM tcg_sales_scan;"
echo "  priced cards in the catalog: 227,371 (2026-10-28)"
echo "  crawl process: $(pgrep -f backfill_sales_volume.py >/dev/null && echo running || echo not running)"
echo "--- log tail"
tail -n 6 "$LOG" 2>/dev/null | sed 's/^/  /'
