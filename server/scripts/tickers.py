# Shared ticker universe for the FinSTR screener.
# Imported by both app.py (live Flask API) and scripts/fetch_data.py (static JSON pipeline)
# so the two never drift out of sync.

SCREENER_TICKERS = [
    # Technology
    "MSFT","ORCL","CRM","ADBE","INTU","NOW","NVDA","AVGO","AMD","TXN","QCOM","INTC",
    "AAPL","HPQ","DELL","CSCO","IBM","ADI","MU","AMAT","LRCX","PANW","CRWD","SNPS","CDNS",
    # Communication Services
    "GOOGL","META","NFLX","T","VZ","TMUS","DIS","CMCSA","WBD",
    # Consumer Discretionary
    "AMZN","EBAY","TSLA","GM","F","MCD","SBUX","CMG","HD","LOW","NKE","BKNG","MAR","ABNB",
    # Consumer Staples
    "PG","CL","KMB","KO","PEP","MDLZ","WMT","COST","MO","PM",
    # Financials
    "JPM","BAC","WFC","C","BRK-B","PGR","AIG","V","MA","PYPL","GS","MS","SCHW","BLK","SPGI","ICE","CME",
    # Healthcare
    "JNJ","PFE","MRK","ABBV","AMGN","GILD","VRTX","UNH","CI","ELV","LLY","TMO","DHR","MDT","ISRG","CVS",
    # Industrials
    "BA","LMT","RTX","CAT","DE","HON","UPS","UNP","MMM","GE","ETN",
    # Energy
    "XOM","CVX","COP","SLB","PSX","MPC","OXY",
    # Utilities
    "NEE","DUK","SO","D","EXC",
    # Real Estate / Materials
    "AMT","PLD","EQIX","LIN","APD","SHW","FCX","NEM","ECL","SPG","O","PSA",
]
