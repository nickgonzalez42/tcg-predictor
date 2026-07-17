# CardStock

A stock market style site for trading cards. Live at [cardstock.guide](https://cardstock.guide).

I collect cards and got tired of guessing whether prices were about to move, so I built the thing I wanted: price history and graded prices for around 68,000 cards across Pokemon, One Piece, Yu-Gi-Oh!, Lorcana, Digimon, and Gundam (Magic is importing as I write this), with machine learned price forecasts at 1 month, 6 months, and 1 year. There's also a portfolio tracker that benchmarks your collection against the S&P 500, which is a fun way to find out your cardboard is outperforming your index fund. Or not.

## How it's put together

Three pieces, one per folder:

**`pipeline/`** is Python. Scrapers pull the card catalogs and art from TCGplayer's public endpoints and all pricing from PriceCharting, politely (serial requests, one per second, resumable if anything dies). A daily job rebuilds the price history, retrains the forecasting models (gradient boosted trees with quantile bands, plus CLIP image embeddings as features, since art similarity carries real signal in collectibles), grades old forecasts against what actually happened, and publishes everything as SQLite files. On Fridays it also writes a market report you can read on the site.

**`dotnet/API/`** is an ASP.NET Core 10 API. It reads those SQLite files, one DbContext per database, and serves the catalog, charts, forecasts, portfolios, comments, and alerts. Auth is ASP.NET Identity with cookies and Google sign in. The only database it writes is the one holding user accounts.

**`client/`** is React 19 with Vite, Redux Toolkit and RTK Query, plain CSS, and lightweight-charts for the TradingView style graphs.

Card art lives in S3 behind CloudFront. The server is a single small EC2 box behind Caddy, and the whole thing costs about $20 a month to run.

## Running it yourself

You can build the API and client easily enough (`dotnet run` in `dotnet/API`, `npm run dev` in `client`), but they expect the SQLite data files the pipeline produces, and those aren't in the repo since they're built from days of polite crawling. The pipeline scripts are all here and documented in `pipeline/README.md` if you want to build your own dataset. Bring patience and respect the rate limits.

## A few things I learned building this

- SQLite in production is great when your data ships as files and one box serves everything. The daily refresh pushes new databases with rsync and the API picks them up on restart.
- If you register an OAuth handler with empty credentials, ASP.NET will 500 every request on every route. Found that one in production.
- CSS transforms re-anchor `position: fixed`. If a scroll library wraps your page in a transform, your fixed popups now belong to it.
- Never chain a git safety check with `;` when you meant `&&`.

Questions or bugs: use the report button on the site, or open an issue here.
